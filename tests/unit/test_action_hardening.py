"""WP-09 / RA-05: the live action path represents uncertainty and rejects racing transitions."""
from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import action_executor as ae  # noqa: E402
import action_queue as aq  # noqa: E402
from actions.base import TERMINAL_STATUSES, VALID_RESULT_STATUSES, VALID_STATUSES, ActionProposal, ActionResult  # noqa: E402


def proposal(pid="p-1"):
    return ActionProposal(
        id=pid, action_type="reminder_create", domain="calendar", title="t", description="d",
        parameters={"text": "x"}, friction="low", min_trust=0, sensitivity="standard", reversible=False,
        undo_window_sec=None, expires_at="2999-01-01T00:00:00+00:00")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_LOCAL_DB", str(tmp_path / "actions.db"))
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    q = aq.ActionQueue(tmp_path)
    yield q
    q.close()


def _to_executing(q, pid="p-1"):
    q.propose(proposal(pid))
    q.transition(pid, "approved", actor="user")
    q.transition(pid, "executing", actor="system:executor")


def test_unknown_is_a_valid_nonterminal_status_and_result():
    assert "unknown" in VALID_STATUSES and "unknown" not in TERMINAL_STATUSES
    assert "unknown" in VALID_RESULT_STATUSES


def test_executing_may_become_unknown_and_unknown_may_be_reconciled(queue):
    _to_executing(queue)
    assert queue.transition("p-1", "unknown", actor="system:executor") == "executing"
    assert queue.transition("p-1", "succeeded", actor="system:reconciler") == "unknown"


def test_unknown_may_resolve_to_failed_and_nothing_else(queue):
    _to_executing(queue)
    queue.transition("p-1", "unknown", actor="system:executor")
    for bad in ("approved", "executing", "pending", "cancelled"):
        with pytest.raises(ValueError):
            queue.transition("p-1", bad, actor="x")
    assert queue.transition("p-1", "failed", actor="system:reconciler") == "unknown"


def test_unknown_result_is_recorded_as_unknown_not_failed(queue):
    _to_executing(queue)
    queue.record_result("p-1", ActionResult(status="unknown", message="timed out", data=None,
                                            reversible=False, reverse_action=None), "2026-10-12T00:00:00+00:00")
    raw = queue.get_raw("p-1")
    assert raw["status"] == "unknown" and raw["result_status"] == "unknown"
    assert raw["idempotency_expires_at"] in (None, "") or raw["idempotency_expires_at"] > "2026-10-12"


def test_idempotency_protection_does_not_expire_on_unknown(queue):
    _to_executing(queue)
    before = queue.get_raw("p-1")["idempotency_expires_at"]
    queue.transition("p-1", "unknown", actor="system:executor")
    assert queue.get_raw("p-1")["idempotency_expires_at"] == before


def test_failed_still_releases_idempotency(queue):
    _to_executing(queue)
    queue.transition("p-1", "failed", actor="system:executor")
    assert queue.get_raw("p-1")["idempotency_expires_at"] is not None


def test_racing_transition_loses_instead_of_overwriting(queue, tmp_path):
    """Two sessions read 'pending'; the second UPDATE must not clobber the first's committed result."""
    import sqlite3
    queue.propose(proposal())
    db = tmp_path / "actions.db"
    state = {"raced": False}

    class Conn:
        def __init__(self, inner):
            self._c = inner

        def execute(self, sql, *a, **k):
            if sql.lstrip().startswith("UPDATE actions SET") and not state["raced"]:
                state["raced"] = True
                rival = sqlite3.connect(db)            # another process commits first
                rival.execute("UPDATE actions SET status='rejected' WHERE id='p-1'")
                rival.commit()
                rival.close()
            return self._c.execute(sql, *a, **k)

        def __enter__(self):
            return self._c.__enter__()

        def __exit__(self, *exc):
            return self._c.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._c, name)
    inner = queue._conn
    queue._conn = Conn(inner)
    try:
        with pytest.raises(aq.ConcurrentTransitionError):
            queue.transition("p-1", "approved", actor="user")
    finally:
        queue._conn = inner
    assert inner.execute("SELECT status FROM actions WHERE id='p-1'").fetchone()[0] == "rejected"


def test_timeout_is_unknown_and_makes_no_false_claim():
    class Slow:
        def execute(self, proposal):
            time.sleep(2)
    result = ae._execute_with_timeout(Slow(), proposal(), 0.05)
    assert result.status == "unknown" and result.data == {"timed_out": True}
    assert "may or may not" in result.message and "No changes were committed" not in result.message


def test_connection_loss_is_unknown_other_exceptions_are_failures():
    class Lost:
        def execute(self, proposal):
            raise ConnectionError("reset by peer")

    class Bad:
        def execute(self, proposal):
            raise ValueError("invalid recipient")
    assert ae._execute_with_timeout(Lost(), proposal(), 5).status == "unknown"
    r = ae._execute_with_timeout(Bad(), proposal(), 5)
    assert r.status == "failure" and "ValueError" in r.message


def test_bridge_status_maps_unknown_through():
    assert ae._bridge_final_status("success") == "succeeded"
    assert ae._bridge_final_status("unknown") == "unknown"
    assert ae._bridge_final_status("failure") == "failed"
    assert ae._bridge_final_status("partial") == "failed"


def test_late_unknown_from_bridge_cannot_downgrade_a_terminal_result(queue):
    _to_executing(queue)
    queue.transition("p-1", "succeeded", actor="system:executor")
    assert queue.apply_remote_result("p-1", "unknown", "late timeout report") is True
    assert queue.get_raw("p-1")["status"] == "succeeded"


def test_unknown_action_keeps_blocking_duplicates_after_the_idempotency_window(queue):
    p = proposal()
    queue.propose(p)
    queue.transition("p-1", "approved", actor="user")
    queue.transition("p-1", "executing", actor="system:executor")
    queue.transition("p-1", "unknown", actor="system:executor")
    # the normal window has long expired ...
    queue._conn.execute("UPDATE actions SET idempotency_expires_at='2000-01-01T00:00:00+00:00' WHERE id='p-1'")
    queue._conn.commit()
    # ... but the uncertain effect still counts as live
    assert queue.find_live_duplicate(p) is not None
    # once reconciled to a terminal result the expired window applies again
    queue.transition("p-1", "failed", actor="system:reconciler")
    queue._conn.execute("UPDATE actions SET idempotency_expires_at='2000-01-01T00:00:00+00:00' WHERE id='p-1'")
    queue._conn.commit()
    assert queue.find_live_duplicate(p) is None


# ── payload binding ─────────────────────────────────────────────────────────

def test_payload_hash_is_stamped_and_stable(queue):
    p = proposal()
    queue.propose(p)
    stored = queue.get_raw("p-1")["payload_hash"]
    assert stored == aq.compute_payload_hash(p) and len(stored) == 64
    p2 = dataclasses.replace(p, parameters={"text": "x", "extra": 1})
    assert aq.compute_payload_hash(p2) != stored


def test_mutated_payload_invalidates_the_approval(queue):
    p = proposal()
    queue.propose(p)
    ok, _ = queue.verify_payload_binding("p-1", p)
    assert ok
    queue._conn.execute("UPDATE actions SET parameters = ? WHERE id = 'p-1'", ('{"text": "SEND MONEY"}',))
    queue._conn.commit()
    changed = queue.get("p-1")
    ok, reason = queue.verify_payload_binding("p-1", changed)
    assert not ok and "changed after it was proposed" in reason


def test_preview_is_bound_to_the_payload_it_showed(queue):
    p = proposal()
    queue.propose(p)
    queue.mark_preview_shown("p-1", "user", notified_channel="cli")
    assert queue.get_raw("p-1")["preview_payload_hash"] == queue.get_raw("p-1")["payload_hash"]
    queue._conn.execute("UPDATE actions SET payload_hash = ? WHERE id='p-1'", (aq.compute_payload_hash(p),))
    other = dataclasses.replace(p, title="a different title")
    ok, reason = queue.verify_payload_binding("p-1", other)
    assert not ok


def test_legacy_rows_without_a_hash_are_accepted(queue):
    p = proposal()
    queue.propose(p)
    queue._conn.execute("UPDATE actions SET payload_hash = NULL WHERE id='p-1'")
    queue._conn.commit()
    assert queue.verify_payload_binding("p-1", p) == (True, "")


def test_approval_records_the_hash_it_approved(queue):
    queue.propose(proposal())
    queue.transition("p-1", "approved", actor="user")
    raw = queue.get_raw("p-1")
    assert raw["approved_payload_hash"] == raw["payload_hash"] and raw["approved_payload_hash"]


def test_remote_ingest_is_stamped(queue):
    p = proposal("r-1")
    assert queue.ingest_remote(p) is True
    assert queue.get_raw("r-1")["payload_hash"] == aq.compute_payload_hash(p)


# ── live trial hold (D-26) ──────────────────────────────────────────────────

REPO_ACTIONS_YAML = Path(__file__).resolve().parents[2] / "config" / "actions.yaml"


def test_held_types_are_read_from_config(tmp_path):
    (tmp_path / "config").mkdir()
    assert ae._load_held_action_types(tmp_path) == frozenset()          # no file: nothing held
    (tmp_path / "config" / "actions.yaml").write_text("live_trial:\n  held: [email_send, slack_send]\nactions: {}\n")
    assert ae._load_held_action_types(tmp_path) == {"email_send", "slack_send"}
    (tmp_path / "config" / "actions.yaml").write_text("live_trial: not-a-mapping\nactions: {}\n")
    assert ae._load_held_action_types(tmp_path) == frozenset()


def test_repo_config_trials_reminders_and_holds_outbound_communication():
    import yaml
    cfg = yaml.safe_load(REPO_ACTIONS_YAML.read_text())
    held = set(cfg["live_trial"]["held"])
    assert cfg["live_trial"]["trial"] == ["reminder_create"]
    assert {"email_send", "email_reply", "whatsapp_send", "slack_send"} <= held
    assert "reminder_create" not in held
    # the trial type always needs a person, whatever the trust level
    assert cfg["actions"]["reminder_create"]["autonomy_floor"] is True
    assert all(t in cfg["actions"] for t in held)                      # no typos in the hold list


def test_held_type_is_queued_but_never_executed(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_LOCAL_DB", str(tmp_path / "actions.db"))
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "actions.yaml").write_text(
        "schema_version: '2.0'\nlive_trial:\n  held: [email_send]\nactions:\n  email_send:\n"
        "    handler: scripts/actions/email_send.py\n    enabled: true\n    min_trust: 1\n")
    ex = ae.ActionExecutor(tmp_path)
    try:
        queued = ex._queue
        held = dataclasses.replace(proposal("h-1"), action_type="email_send")
        queued.propose(held)
        result = ex.approve("h-1", approved_by="user:test")
        assert result.status == "failure" and "held until promoted" in result.message
        assert queued.get_raw("h-1")["status"] == "pending"                 # untouched: no approval recorded
    finally:
        ex.close()
