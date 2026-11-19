"""
tests/unit/test_action_queue.py — Unit tests for ActionQueue lifecycle.

Tests the SQLite-backed action queue: propose, transition, state machine,
deduplication, expiry, archival, and audit trail.

Ref: specs/act.md §2, §3
"""
from datetime import datetime, timedelta, timezone
import dataclasses
import sqlite3
import subprocess
import sys

import pytest


from action_queue import ActionQueue, compute_payload_hash
from actions.base import (
    TERMINAL_STATUSES,
    VALID_RESULT_STATUSES,
    VALID_STATUSES,
    ActionProposal,
    ActionResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Return a temporary ActionQueue backed by an in-memory-like temp DB."""
    db_path = tmp_path / "actions.db"
    q = ActionQueue(db_path)
    yield q
    q.close()


def _make_proposal(
    action_type: str = "email_send",
    domain: str = "test",
    title: str = "Test action",
    **kwargs,
) -> ActionProposal:
    import uuid
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type=action_type,
        domain=domain,
        title=title,
        description="Test description",
        parameters={"to": "test@example.com", "subject": "Hi", "body": "Hello"},
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=30,
        expires_at=None,
        source_step="12.5",
        source_skill="test",
        linked_oi=None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Propose tests
# ---------------------------------------------------------------------------

class TestPropose:
    def test_propose_creates_pending_entry(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        row = tmp_db.get_raw(p.id)
        assert row is not None
        assert row["status"] == "pending"
        assert row["action_type"] == "email_send"

    def test_propose_deduplication_same_type_domain(self, tmp_db):
        p1 = _make_proposal(action_type="email_send", domain="kids")
        p2 = _make_proposal(action_type="email_send", domain="kids")
        tmp_db.propose(p1)
        with pytest.raises(ValueError, match="Duplicate"):
            tmp_db.propose(p2)

    def test_propose_different_domains_allowed(self, tmp_db):
        p1 = _make_proposal(action_type="email_send", domain="kids")
        p2 = _make_proposal(action_type="email_send", domain="finance")
        tmp_db.propose(p1)
        tmp_db.propose(p2)  # Should not raise
        assert tmp_db.get_raw(p1.id) is not None
        assert tmp_db.get_raw(p2.id) is not None

    def test_propose_different_types_allowed(self, tmp_db):
        p1 = _make_proposal(action_type="email_send", domain="test")
        p2 = _make_proposal(action_type="reminder_create", domain="test")
        tmp_db.propose(p1)
        tmp_db.propose(p2)  # Should not raise
        assert tmp_db.get_raw(p2.id) is not None

    def test_propose_with_expires_at(self, tmp_db):
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        import uuid
        p = ActionProposal(
            id=str(uuid.uuid4()),
            action_type="calendar_create",
            domain="test",
            title="Expiring action",
            description="",
            parameters={},
            friction="low",
            min_trust=0,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=expires,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        tmp_db.propose(p)
        row = tmp_db.get_raw(p.id)
        assert row["expires_at"] == expires


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_pending_to_approved(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        prev_status = tmp_db.transition(p.id, "approved", actor="user")
        assert prev_status == "pending"  # transition() returns the from_status
        assert tmp_db.get_raw(p.id)["status"] == "approved"

    def test_pending_to_rejected(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "rejected", actor="user")
        assert tmp_db.get_raw(p.id)["status"] == "rejected"

    def test_invalid_transition_raises(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        # pending → succeeded is invalid
        with pytest.raises(ValueError, match="Invalid transition"):
            tmp_db.transition(p.id, "succeeded", actor="user")

    def test_terminal_state_cannot_transition(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "rejected", actor="user")
        with pytest.raises(ValueError, match=r"is\ in\ terminal\ state\ 'rejected';\ no\ further\ transitions\ allowed"):
            tmp_db.transition(p.id, "approved", actor="user")

    def test_approved_to_executing(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "approved", actor="user")
        tmp_db.transition(p.id, "executing", actor="executor")
        assert tmp_db.get_raw(p.id)["status"] == "executing"

    def test_executing_to_succeeded(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "approved", actor="user")
        tmp_db.transition(p.id, "executing", actor="executor")
        tmp_db.transition(p.id, "succeeded", actor="executor")
        assert tmp_db.get_raw(p.id)["status"] == "succeeded"

    def test_pending_to_deferred(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "deferred", actor="user")
        assert tmp_db.get_raw(p.id)["status"] == "deferred"


# ---------------------------------------------------------------------------
# List pending tests
# ---------------------------------------------------------------------------

class TestListPending:
    def test_list_pending_returns_only_pending(self, tmp_db):
        p1 = _make_proposal(action_type="email_send", domain="test1")
        p2 = _make_proposal(action_type="reminder_create", domain="test2")
        tmp_db.propose(p1)
        tmp_db.propose(p2)
        tmp_db.transition(p2.id, "rejected", actor="user")
        pending = tmp_db.list_pending()
        # list_pending() returns ActionProposal objects, not dicts
        ids = [proposal.id for proposal in pending]
        assert p1.id in ids
        assert p2.id not in ids

    def test_list_pending_empty(self, tmp_db):
        assert tmp_db.list_pending() == []


# ---------------------------------------------------------------------------
# Record result tests
# ---------------------------------------------------------------------------

class TestRecordResult:
    def test_record_result_stores_success(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "approved", actor="user")
        tmp_db.transition(p.id, "executing", actor="executor")
        # Do NOT manually transition to succeeded — record_result() does that internally
        result = ActionResult(
            status="success",
            message="Done",
            data={"message_id": "abc123"},
            reversible=True,
            reverse_action=None,
        )
        executed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp_db.record_result(p.id, result, executed_at)
        row = tmp_db.get_raw(p.id)
        assert row["status"] == "succeeded"
        assert row["result_status"] == "success"
        assert "Done" in (row["result_message"] or "")


# ---------------------------------------------------------------------------
# Expiry sweep tests
# ---------------------------------------------------------------------------

class TestExpirySweep:
    def test_expire_stale_past_deadline(self, tmp_db):
        import uuid
        # Action already expired
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        p = ActionProposal(
            id=str(uuid.uuid4()),
            action_type="reminder_create",
            domain="test",
            title="Expiring",
            description="",
            parameters={},
            friction="low",
            min_trust=0,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=past,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        tmp_db.propose(p)
        count = tmp_db.expire_stale()
        assert count >= 1
        assert tmp_db.get_raw(p.id)["status"] == "expired"

    def test_expire_stale_not_expired(self, tmp_db):
        import uuid
        # Action with future expiry — should NOT expire
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        p = ActionProposal(
            id=str(uuid.uuid4()),
            action_type="reminder_create",
            domain="test",
            title="Not expiring",
            description="",
            parameters={},
            friction="low",
            min_trust=0,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=future,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        tmp_db.propose(p)
        tmp_db.expire_stale()
        assert tmp_db.get_raw(p.id)["status"] == "pending"


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_entry_created_on_propose(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        # Audit table has at least one entry for this action — use _conn directly
        rows = tmp_db._conn.execute(
            "SELECT * FROM action_audit WHERE action_id = ?", (p.id,)
        ).fetchall()
        assert len(rows) >= 1
        assert rows[0]["to_status"] == "pending"

    def test_audit_entry_created_on_transition(self, tmp_db):
        p = _make_proposal()
        tmp_db.propose(p)
        tmp_db.transition(p.id, "approved", actor="test-actor")
        rows = tmp_db._conn.execute(
            "SELECT * FROM action_audit WHERE action_id = ? AND to_status = 'approved'",
            (p.id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["actor"] == "test-actor"


# ---------------------------------------------------------------------------
# Uncertainty, racing transitions, idempotency & payload binding (RA-05)
# ---------------------------------------------------------------------------

def _hardened_proposal(pid: str = "p-1") -> ActionProposal:
    return ActionProposal(
        id=pid, action_type="reminder_create", domain="calendar", title=f"t-{pid}", description="d",
        parameters={"text": pid}, friction="low", min_trust=0, sensitivity="standard", reversible=False,
        undo_window_sec=None, expires_at="2999-01-01T00:00:00+00:00", idempotency_key=f"key-{pid}",
    )


@pytest.fixture
def hardened_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_LOCAL_DB", str(tmp_path / "actions.db"))
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    q = ActionQueue(tmp_path)
    yield q
    q.close()


def _to_executing(q: ActionQueue, pid: str = "p-1") -> None:
    q.propose(_hardened_proposal(pid))
    q.transition(pid, "approved", actor="user")
    q.transition(pid, "executing", actor="system:executor")


class TestQueueUncertaintyAndPayloadBinding:
    def test_unknown_is_valid_nonterminal_status_and_result(self):
        assert "unknown" in VALID_STATUSES and "unknown" not in TERMINAL_STATUSES
        assert "unknown" in VALID_RESULT_STATUSES

    def test_status_and_receipt_rollback_together(self, hardened_queue):
        _to_executing(hardened_queue)
        hardened_queue._conn.execute(
            "CREATE TRIGGER fail_receipt BEFORE UPDATE OF result_status ON actions "
            "BEGIN SELECT RAISE(ABORT, 'simulated full disk'); END"
        )
        hardened_queue._conn.commit()
        with pytest.raises(sqlite3.DatabaseError):
            hardened_queue.record_result(
                "p-1", ActionResult(status="success", message="done", data={"id": "r"},
                                    reversible=False, reverse_action=None),
                "2026-10-12T00:00:00+00:00",
            )
        row = hardened_queue.get_raw("p-1")
        assert row["status"] == "executing" and row["result_status"] is None

    def test_unknown_reconciliation_and_allowed_transitions(self, hardened_queue):
        _to_executing(hardened_queue, "p-ok")
        assert hardened_queue.transition("p-ok", "unknown", actor="system:executor") == "executing"
        assert hardened_queue.transition("p-ok", "succeeded", actor="system:reconciler") == "unknown"

        _to_executing(hardened_queue, "p-bad")
        hardened_queue.transition("p-bad", "unknown", actor="system:executor")
        for bad in ("approved", "executing", "pending", "cancelled"):
            with pytest.raises(ValueError, match=r"Invalid transition 'unknown'"):
                hardened_queue.transition("p-bad", bad, actor="x")
        assert hardened_queue.transition("p-bad", "failed", actor="system:reconciler") == "unknown"

    def test_consumed_dispatch_recovery_marks_stranded_execution_unknown(self, hardened_queue):
        _to_executing(hardened_queue)
        hardened_queue._conn.execute(
            "INSERT INTO action_dispatch_receipts(operation_key,action_id) VALUES('operation','p-1')"
        )
        hardened_queue._conn.commit()

        assert hardened_queue.recover_dispatch_unknown(
            "p-1", "2026-10-12T00:00:00+00:00", require_consumed_dispatch=True
        ) is True
        row = hardened_queue.get_raw("p-1")
        assert row["status"] == "unknown"
        assert row["result_status"] == "unknown"
        assert hardened_queue.find_live_duplicate(_hardened_proposal()) is not None

    def test_unknown_preserves_idempotency_while_failed_releases(self, hardened_queue):
        _to_executing(hardened_queue, "p-1")
        hardened_queue.record_result(
            "p-1", ActionResult(status="unknown", message="timed out", data=None,
                                reversible=False, reverse_action=None),
            "2026-10-12T00:00:00+00:00",
        )
        raw = hardened_queue.get_raw("p-1")
        assert raw["status"] == "unknown" and raw["result_status"] == "unknown"
        assert raw["idempotency_expires_at"] in (None, "") or raw["idempotency_expires_at"] > "2026-10-12"
        hardened_queue._conn.execute(
            "UPDATE actions SET idempotency_expires_at='2000-01-01T00:00:00+00:00' WHERE id='p-1'"
        )
        hardened_queue._conn.commit()
        assert hardened_queue.find_live_duplicate(_hardened_proposal("p-1")) is not None
        hardened_queue.transition("p-1", "failed", actor="system:reconciler")
        assert hardened_queue.get_raw("p-1")["idempotency_expires_at"] is not None

    def test_racing_transition_loses_instead_of_overwriting(self, hardened_queue, tmp_path):
        hardened_queue.propose(_hardened_proposal())
        db = tmp_path / "actions.db"
        state = {"raced": False}

        class Conn:
            def __init__(self, inner):
                self._c = inner

            def execute(self, sql, *a, **k):
                if sql.strip() == "BEGIN IMMEDIATE" and not state["raced"]:
                    state["raced"] = True
                    subprocess.run(
                        [sys.executable, "-c",
                         "import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); "
                         "db.execute(\"UPDATE actions SET status='rejected' WHERE id='p-1'\"); "
                         "db.commit(); db.close()", str(db)],
                        check=True, capture_output=True, timeout=15,
                    )
                return self._c.execute(sql, *a, **k)

            def __enter__(self):
                return self._c.__enter__()

            def __exit__(self, *exc):
                return self._c.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._c, name)

        inner = hardened_queue._conn
        hardened_queue._conn = Conn(inner)
        try:
            with pytest.raises(ValueError, match="terminal state 'rejected'"):
                hardened_queue.transition("p-1", "approved", actor="user")
        finally:
            hardened_queue._conn = inner
        assert inner.execute("SELECT status FROM actions WHERE id='p-1'").fetchone()[0] == "rejected"

    def test_late_unknown_from_bridge_cannot_downgrade_terminal(self, hardened_queue):
        _to_executing(hardened_queue)
        hardened_queue.transition("p-1", "succeeded", actor="system:executor")
        assert hardened_queue.apply_remote_result("p-1", "unknown", "late timeout report") is True
        assert hardened_queue.get_raw("p-1")["status"] == "succeeded"

    def test_payload_hash_binding_preview_and_approval(self, hardened_queue):
        p = _hardened_proposal()
        hardened_queue.propose(p)
        stored = hardened_queue.get_raw("p-1")["payload_hash"]
        assert stored == compute_payload_hash(p) and len(stored) == 64
        assert compute_payload_hash(dataclasses.replace(p, parameters={"text": "p-1", "extra": 1})) != stored
        assert hardened_queue.verify_payload_binding("p-1", p)[0] is True

        hardened_queue._conn.execute("UPDATE actions SET parameters = ? WHERE id = 'p-1'", ('{"text": "SEND MONEY"}',))
        hardened_queue._conn.commit()
        ok, reason = hardened_queue.verify_payload_binding("p-1", hardened_queue.get("p-1"))
        assert not ok and "changed after it was proposed" in reason

        p2 = _hardened_proposal("p-2")
        hardened_queue.propose(p2)
        with pytest.raises(ValueError, match=r"proposal changed before preview could be recorded"):
            hardened_queue.mark_preview_shown("p-2", "user", payload_hash="wrong")
        hardened_queue.mark_preview_shown("p-2", "user", notified_channel="cli", payload_hash=compute_payload_hash(p2))
        assert hardened_queue.verify_payload_binding("p-2", dataclasses.replace(p2, title="other"))[0] is False

        p3 = dataclasses.replace(_hardened_proposal("p-3"), preview_required=True,
                                 preview_shown_at="2026-10-21T00:00:00Z", preview_shown_by="system:direct_propose")
        hardened_queue.propose(p3)
        assert not hardened_queue.verify_payload_binding("p-3", p3)[0]

        hardened_queue.propose(_hardened_proposal("p-4"))
        hardened_queue.transition("p-4", "approved", actor="user")
        raw = hardened_queue.get_raw("p-4")
        assert raw["approved_payload_hash"] == raw["payload_hash"] and raw["approved_payload_hash"]

        hardened_queue._conn.execute("UPDATE actions SET payload_hash = NULL WHERE id='p-4'")
        hardened_queue._conn.commit()
        assert hardened_queue.verify_payload_binding("p-4", _hardened_proposal("p-4"))[0] is False

        p_remote = _hardened_proposal("r-1")
        assert hardened_queue.ingest_remote(p_remote) is True
        assert hardened_queue.get_raw("r-1")["payload_hash"] == compute_payload_hash(p_remote)
