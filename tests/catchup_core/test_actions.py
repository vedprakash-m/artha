from __future__ import annotations

import sqlite3
import threading

import pytest

from catchup_core.actions import (
    ActionError, ActionStatus, ActionStore, ClaimRefused, OperationState, canonical_hash,
)

FUTURE = "2999-01-01T00:00:00Z"
PAST = "2000-01-01T00:00:00Z"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "actions.db"


_OPEN: list[sqlite3.Connection] = []


@pytest.fixture(autouse=True)
def _close_connections():
    yield
    while _OPEN:
        _OPEN.pop().close()


def store(db, gen=1, clock=None):
    conn = sqlite3.connect(db, timeout=10, check_same_thread=False)
    _OPEN.append(conn)
    return ActionStore(conn, authority_generation=gen, **({"clock": clock} if clock else {}))


def make(s, pid="p1", op="cal:2026-10-14:dentist", payload=None, expires=FUTURE):
    payload = payload or {"title": "Dentist", "when": "2026-10-14T15:30:00-07:00"}
    h = s.propose(pid, operation_key=op, payload=payload, expires_at=expires)
    return h


def approved(s, pid="p1", **kw):
    h = make(s, pid, **kw)
    s.approve(pid, payload_hash=h, approver="ved", nonce="n1")
    return h


def test_canonical_hash_is_order_independent_and_sensitive():
    assert canonical_hash({"a": 1, "b": [2, 3]}) == canonical_hash({"b": [2, 3], "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_happy_path_lifecycle_and_audit_trail(db):
    s = store(db)
    approved(s)
    c = s.claim("p1")
    s.mark_dispatched(c)
    assert s.record_outcome(c, result="success", provider_request_id="req-1", receipt_ref="evt-9") is ActionStatus.SUCCEEDED
    assert s.transitions("p1") == [(None, "PROPOSED"), ("PROPOSED", "APPROVED"), ("APPROVED", "EXECUTING"),
                                   ("EXECUTING", "SUCCEEDED")]
    reg = s._conn.execute("SELECT state, receipt_ref FROM operation_registry").fetchone()
    assert reg["state"] == OperationState.CONFIRMED and reg["receipt_ref"] == "evt-9"


def test_approval_must_match_the_reviewed_payload(db):
    s = store(db)
    make(s)
    with pytest.raises(ActionError, match="does not match"):
        s.approve("p1", payload_hash="0" * 64, approver="ved", nonce="n")
    assert s.status("p1") is ActionStatus.PROPOSED


def test_payload_change_after_approval_invalidates_it(db):
    s = store(db)
    approved(s)
    new_hash = s.revise_payload("p1", {"title": "Dentist", "when": "2026-10-15T09:00:00-07:00"})
    assert s.status("p1") is ActionStatus.PROPOSED
    with pytest.raises(ClaimRefused):
        s.claim("p1")
    s.approve("p1", payload_hash=new_hash, approver="ved", nonce="n2")
    assert s.claim("p1").payload_hash == new_hash


def test_cannot_revise_in_flight_or_unknown(db):
    s = store(db)
    approved(s)
    c = s.claim("p1")
    with pytest.raises(ActionError):
        s.revise_payload("p1", {"x": 1})
    s.record_outcome(c, result="unknown")
    with pytest.raises(ActionError):
        s.revise_payload("p1", {"x": 1})


def test_concurrent_claims_dispatch_exactly_once(db):
    s = store(db)
    approved(s)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        mine = store(db)
        try:
            mine.claim("p1")
            res = "won"
        except ClaimRefused:
            res = "lost"
        mine._conn.close()
        with lock:
            outcomes.append(res)
    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("won") == 1 and outcomes.count("lost") == 5


def test_two_proposals_for_one_operation_cannot_both_dispatch(db):
    s = store(db)
    approved(s, "p1", op="send:rsvp:evt1")
    approved(s, "p2", op="send:rsvp:evt1")
    s.claim("p1")
    with pytest.raises(ClaimRefused, match="owns this operation"):
        s.claim("p2")


def test_confirmed_failure_releases_the_key_for_a_new_proposal(db):
    s = store(db)
    approved(s, "p1", op="op1")
    c = s.claim("p1")
    s.mark_dispatched(c)
    assert s.record_outcome(c, result="failure", error_class="provider") is ActionStatus.FAILED
    approved(s, "p2", op="op1")
    assert s.claim("p2").proposal_id == "p2"


def test_timeout_is_unknown_not_failed_and_blocks_redispatch(db):
    s = store(db)
    approved(s)
    c = s.claim("p1")
    s.mark_dispatched(c)
    assert s.record_outcome(c, result="unknown", error_class="timeout") is ActionStatus.UNKNOWN
    assert s.status("p1") is ActionStatus.UNKNOWN
    with pytest.raises(ClaimRefused):
        s.claim("p1")
    # a regenerated proposal for the same effect is also refused: the key is still held
    approved(s, "p2", op="cal:2026-10-14:dentist")
    with pytest.raises(ClaimRefused, match="already unknown|owns this operation"):
        s.claim("p2")


def test_reconcile_confirms_or_releases(db):
    s = store(db)
    approved(s, "p1", op="a")
    c = s.claim("p1")
    s.record_outcome(c, result="unknown")
    assert s.reconcile("p1", lambda op, h: "evt-123") is ActionStatus.SUCCEEDED

    approved(s, "p2", op="b")
    c2 = s.claim("p2")
    s.record_outcome(c2, result="unknown")
    assert s.reconcile("p2", lambda op, h: None) is ActionStatus.FAILED
    approved(s, "p3", op="b")                       # the key was released
    assert s.claim("p3")


def test_reconcile_failure_stays_unknown(db):
    s = store(db)
    approved(s)
    s.record_outcome(s.claim("p1"), result="unknown")

    def offline(op, h):
        raise ConnectionError("provider unreachable")
    assert s.reconcile("p1", offline) is ActionStatus.UNKNOWN
    with pytest.raises(ActionError):
        s.reconcile("nope", lambda o, h: None)


def test_reconcile_only_applies_to_unknown(db):
    s = store(db)
    approved(s)
    with pytest.raises(ActionError):
        s.reconcile("p1", lambda o, h: "x")


def test_expiry_blocks_approval_and_claim(db):
    s = store(db, clock=lambda: "2026-10-12T00:00:00Z")
    make(s, expires=PAST)
    h = s._row("p1")["payload_hash"]
    with pytest.raises(ActionError, match="expired"):
        s.approve("p1", payload_hash=h, approver="ved", nonce="n")
    assert s.status("p1") is ActionStatus.EXPIRED
    # approved but then expired before dispatch
    t = {"now": "2026-10-12T00:00:00Z"}
    s2 = store(db.with_name("b.db"), clock=lambda: t["now"])
    approved(s2, "q1", expires="2026-10-13T00:00:00Z")
    t["now"] = "2026-10-14T00:00:00Z"
    with pytest.raises(ClaimRefused, match="expired"):
        s2.claim("q1")
    assert s2.expire_due() == 1


def test_stale_authority_generation_cannot_execute(db):
    old = store(db, gen=1)
    approved(old)
    promoted = store(db, gen=2)
    with pytest.raises(ClaimRefused, match="authority generation"):
        promoted.claim("p1")


def test_restore_makes_in_flight_work_non_dispatchable(db):
    s = store(db)
    approved(s, "p1", op="a")
    approved(s, "p2", op="b")
    s.claim("p2")                                    # EXECUTING at snapshot time
    assert s.mark_restored() == 2
    assert s.status("p2") is ActionStatus.UNKNOWN
    with pytest.raises(ClaimRefused, match="not dispatchable"):
        s.claim("p1")


def test_unknown_proposal_and_bad_inputs(db):
    s = store(db)
    with pytest.raises(ActionError):
        s.status("nope")
    approved(s)
    c = s.claim("p1")
    with pytest.raises(ActionError):
        s.record_outcome(c, result="maybe")
    s.record_outcome(c, result="success")
    with pytest.raises(ActionError, match="not EXECUTING"):
        s.record_outcome(c, result="success")
    with pytest.raises(ActionError):
        s.approve("p1", payload_hash="x", approver="v", nonce="n")


def test_connection_is_fully_synchronous(db):
    s = store(db)
    assert s._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
