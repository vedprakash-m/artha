"""
tests/unit/test_action_queue.py — Unit tests for ActionQueue lifecycle.

Tests the SQLite-backed action queue: propose, transition, state machine,
deduplication, expiry, archival, and audit trail.

Ref: specs/act.md §2, §3
"""
import json
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from action_queue import ActionQueue
from actions.base import ActionProposal, ActionResult


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
        with pytest.raises(ValueError):
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
