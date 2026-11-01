"""Resource and lifecycle controls: actual provider usage, durable budget limits, fair backlog, anti-resurrection (Row 5 / RA-10 / RA-16)."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from catchup_core import crypto, ids
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import Confidentiality, ProcessingState
from catchup_core.coordinator import fair_backlog_slice
from catchup_core.ledger import Ledger, SignalInput
from catchup_core.metrics import (
    estimate_cost,
)
from catchup_core.retention import audit_anti_resurrection, check_anti_resurrection
from catchup_core.resources import AdmissionStore, ResourceLimit, ResourcePolicy


def _run(seconds: float = 1.0, status: str = "complete", **extra: Any) -> dict[str, Any]:
    start = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    return {
        "total_seconds": seconds,
        "status": status,
        "stages": {"FETCH": seconds / 2, "EXTRACT": seconds / 2},
        "catchup_id": "cu_resource_test",
        "resumed": False,
        "at": start.isoformat(),
        "ended_at": (start + timedelta(seconds=seconds)).isoformat(),
        "model_requests": 1,
        "prompt_chars": 1000,
        "answer_chars": 200,
        **extra,
    }


# ── 1. Actual provider usage separate from estimates ─────────────────────────

def test_actual_provider_usage_reported_separately_when_verified():
    verified_run = _run(
        actual_prompt_tokens=250,
        actual_completion_tokens=50,
        actual_cost_usd=0.0045,
        actual_usage_verified=True,
    )
    pricing = {
        "input_usd_per_million_tokens": 3.0,
        "output_usd_per_million_tokens": 15.0,
        "as_of": "2026-10-24",
        "chars_per_token": 4,
        "runs_per_month": 30,
    }
    cost = estimate_cost([verified_run], pricing)
    assert cost["actual_usage_verified"] is True
    assert cost["actual_prompt_tokens"] == 250
    assert cost["actual_completion_tokens"] == 50
    assert cost["actual_cost_usd"] == 0.0045
    assert cost["actual_usd_per_run"] == 0.0045
    assert cost["actual_projected_monthly_usd"] == round(0.0045 * 30, 2)
    assert cost["actual_within_monthly_budget"] is True
    # Character estimate remains present alongside verified actuals
    assert "usd_per_run" in cost
    assert "projected_monthly_usd" in cost


def test_mixed_verified_and_unverified_runs_keeps_aggregate_unverified():
    run1 = _run(actual_prompt_tokens=100, actual_completion_tokens=20, actual_cost_usd=0.001, actual_usage_verified=True, catchup_id="c1")
    run2 = _run(catchup_id="c2")  # unverified character-only run
    pricing = {"input_usd_per_million_tokens": 3.0, "output_usd_per_million_tokens": 15.0, "as_of": "2026-10-24"}
    cost = estimate_cost([run1, run2], pricing)
    assert cost["actual_usage_verified"] is False
    assert "actual_usd_per_run" not in cost


# ── 2. Durable budget limits and monthly accrual ───────────────────────────────

def test_monthly_reservations_are_enforced_by_the_durable_admission_store(isolated_ledger):
    ledger, _, _ = isolated_ledger
    sid = ledger.upsert_signal(SignalInput("mail", "one", "m1", "v1", "2026-10-24T12:00:00Z",
                                          "synthetic-hmac", "synthetic-blob"))[0]
    store = AdmissionStore(ledger, ResourcePolicy.from_mapping(
        {"max_requests_per_month": 1, "max_pending_signals": 4}))
    now = datetime(2026, 10, 24, tzinfo=UTC)
    assert store.admit("c1", sid, "r1", 400, now)
    with pytest.raises(ResourceLimit, match="month"):
        store.admit("c2", sid, "r2", 400, now)
    usage = store.month_usage("2026-10")
    assert usage.requests == usage.unknown_requests == 1
    assert usage.actual_tokens == 0 and usage.estimated_tokens == 100


# ── 3. Fair bounded backlog ───────────────────────────────────────────────────

def test_fair_backlog_slice_prevents_starvation_across_connectors():
    # Simulate a fake Row using a dict-backed Mapping
    class FakeRow(Mapping[str, Any]):
        def __init__(self, signal_id: str, connector: str):
            self._d = {"signal_id": signal_id, "connector": connector}
        def __getitem__(self, k: str) -> Any:
            return self._d[k]
        def __iter__(self):
            return iter(self._d)
        def __len__(self):
            return len(self._d)
        def keys(self):
            return self._d.keys()

    # Connector A has 100 pending signals, Connector B has 3
    rows = [FakeRow(f"sig_a_{i}", "gmail") for i in range(100)]
    rows += [FakeRow(f"sig_b_{i}", "calendar") for i in range(3)]

    # Slice with limit 10
    sliced = fair_backlog_slice(rows, limit=10)
    assert len(sliced) == 10
    connectors = [r["connector"] for r in sliced]
    # Calendar should NOT be starved: it had 3 items and gets all 3 in the first rounds
    assert connectors.count("calendar") == 3
    assert connectors.count("gmail") == 7


def test_fair_backlog_slice_noop_when_under_limit():
    class FakeRow(Mapping[str, Any]):
        def __init__(self, signal_id: str, connector: str):
            self._d = {"signal_id": signal_id, "connector": connector}
        def __getitem__(self, k: str) -> Any:
            return self._d[k]
        def __iter__(self):
            return iter(self._d)
        def __len__(self):
            return len(self._d)
        def keys(self):
            return self._d.keys()

    rows = [FakeRow(f"sig_{i}", "gmail") for i in range(5)]
    assert fair_backlog_slice(rows, limit=10) == rows
    assert fair_backlog_slice(rows, limit=0) == rows


# ── 4. Anti-resurrection retention checks ─────────────────────────────────────

@pytest.fixture
def isolated_ledger(tmp_path):
    key = ids.HmacKey(1, b"k" * 32)
    cipher = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"c" * 32}))
    blobs = EncryptedBlobStore(tmp_path / "blobs", cipher, key)
    ledger = Ledger(
        tmp_path / "ledger.db",
        id_key=key,
        cipher=cipher,
        owner_id="owner",
        consent_subjects=frozenset({"owner"}),
        known_subjects=frozenset({"owner"}),
        blob_deleter=blobs.delete,
    ).open()
    ledger.migrate()
    yield ledger, blobs, tmp_path
    ledger.close()


def test_anti_resurrection_blocks_tombstoned_and_purged_items(isolated_ledger):
    ledger, blobs, _ = isolated_ledger
    # Register an active signal
    ref = blobs.put("test_msg", "secret content")
    chmac = ids.content_hmac(ledger._id_key, "secret content")
    sid, is_new = ledger.upsert_signal(
        SignalInput("gmail", "primary", "msg_123", "v1", "2026-10-24T12:00:00Z", chmac, ref, Confidentiality.SENSITIVE)
    )
    assert is_new is True
    assert check_anti_resurrection(ledger, "gmail", "primary", "msg_123", chmac) is True

    # Forget the signal to record a tombstone
    ledger.forget("gmail", "primary", "msg_123", content_hmac=chmac, reason="owner requested erasure")
    # Anti-resurrection check MUST now deny it
    assert check_anti_resurrection(ledger, "gmail", "primary", "msg_123", chmac) is False


def test_audit_anti_resurrection_flags_sources_and_cache(isolated_ledger):
    ledger, blobs, _ = isolated_ledger
    chmac = ids.content_hmac(ledger._id_key, "deleted text")
    ledger.forget("gmail", "primary", "msg_erased", content_hmac=chmac, reason="erasure")

    candidates = [
        {"connector": "gmail", "account_scope": "primary", "upstream_id": "msg_active", "content_hmac": "live_hmac"},
        {"connector": "gmail", "account_scope": "primary", "upstream_id": "msg_erased", "content_hmac": chmac},
    ]
    cache = [
        {"connector": "gmail", "account_scope": "primary", "upstream_id": "msg_erased", "content_hmac": chmac},
        {"connector": "gmail", "account_scope": "primary", "upstream_id": "msg_other", "content_hmac": "safe"},
    ]

    suppressed, violations = audit_anti_resurrection(ledger, candidate_records=candidates, cache_entries=cache)
    assert suppressed == ["msg_erased", "msg_erased"]
    assert len(violations) == 2
    assert any("matches active tombstone" in v for v in violations)


# ── 5. Sustained query workload measurements ──────────────────────────────────

def test_repeated_signal_queries_preserve_reconciled_results(isolated_ledger):
    ledger, blobs, _ = isolated_ledger
    # Insert multiple signals and perform repeated permission-checked queries
    facts_count = 50
    for i in range(facts_count):
        ref = blobs.put(f"msg_{i}", f"content {i}")
        chmac = ids.content_hmac(ledger._id_key, f"content {i}")
        sid, _ = ledger.upsert_signal(
            SignalInput("gmail", "primary", f"msg_{i}", "v1", "2026-10-24T12:00:00Z", chmac, ref, Confidentiality.ORDINARY)
        )
        ledger.set_signal_subjects(sid, ("owner",))
        ledger.set_processing_state(sid, ProcessingState.RECONCILED)

    queries = 200
    for _ in range(queries):
        rows = ledger.conn.execute("SELECT signal_id FROM signals WHERE processing_state='reconciled' LIMIT 20").fetchall()
        assert len(rows) == 20


def test_backlog_fairness_uses_sqlite_row_column_names():
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT 'a' AS connector, 'one' AS signal_id UNION ALL "
                          "SELECT 'a', 'two' UNION ALL SELECT 'b', 'three'").fetchall()
    assert [row['signal_id'] for row in fair_backlog_slice(rows, 2)] == ['one', 'three']
