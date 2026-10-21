from __future__ import annotations

import sqlite3

import pytest

from catchup_core.action_mapping import LEGACY_MAP, audit_database, plan_migration
from catchup_core.actions import ActionStatus


def test_every_legacy_status_has_a_target():
    legacy = {"pending", "modifying", "approved", "rejected", "deferred", "executing", "succeeded", "failed",
              "expired", "cancelled"}
    assert set(LEGACY_MAP) == legacy | {"unknown"}
    assert all(isinstance(v, ActionStatus) for v in LEGACY_MAP.values())


def test_in_flight_and_ambiguous_failures_are_uncertain_not_failed():
    plan = plan_migration([
        {"id": "a", "status": "executing"},
        {"id": "b", "status": "failed", "result_message": "request timed out after 30s"},
        {"id": "c", "status": "failed", "result_message": "HTTP 400: invalid recipient"},
        {"id": "d", "status": "failed", "result_status": "partial"},
        {"id": "e", "status": "approved"},
        {"id": "f", "status": "succeeded"},
    ])
    by = {r.action_id: r for r in plan.rows}
    assert by["a"].target is ActionStatus.UNKNOWN and by["a"].needs_review
    assert by["b"].target is ActionStatus.UNKNOWN
    assert by["c"].target is ActionStatus.FAILED and not by["c"].needs_review
    assert by["d"].target is ActionStatus.UNKNOWN
    assert by["e"].target is ActionStatus.APPROVED and "re-approve" in by["e"].reason
    assert by["f"].target is ActionStatus.SUCCEEDED
    assert len(plan.review) == 4 and plan.safe_to_migrate


def test_unknown_statuses_block_the_migration():
    plan = plan_migration([{"id": "x", "status": "limbo"}])
    assert plan.unmapped == ("x:limbo",) and not plan.safe_to_migrate


def test_audit_database_is_read_only_and_counts(tmp_path):
    db = tmp_path / "actions.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE actions(id TEXT, status TEXT, result_status TEXT, result_message TEXT)")
    c.executemany("INSERT INTO actions VALUES(?,?,?,?)", [("1", "pending", None, None), ("2", "expired", None, None),
                                                          ("3", "executing", None, None)])
    c.commit()
    c.close()
    before = db.read_bytes()
    plan = audit_database(db)
    assert plan.counts == {"PROPOSED": 1, "EXPIRED": 1, "UNKNOWN": 1}
    assert db.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        audit_database(tmp_path / "missing.db")


def test_activity_lines_use_the_spec_vocabulary_and_never_guess():
    from catchup_core.action_mapping import describe
    pending = describe({"id": "a", "action_type": "reminder_create", "domain": "home", "status": "pending",
                        "reversible": 1, "undo_window_sec": 30, "created_at": "t"})
    assert (pending["lifecycle"], pending["group"]) == ("PROPOSED", "pending")
    assert pending["reversibility"] == "reversible (undo window 30s)"
    assert describe({"status": "executing"})["group"] == "executing"           # in flight is not "lost"
    assert describe({"status": "unknown"})["group"] == "unknown"
    assert describe({"status": "succeeded", "reversible": 1})["reversibility"] == "reversible"
    odd = describe({"status": "from-the-future"})
    assert (odd["lifecycle"], odd["group"], odd["reversibility"]) == ("UNKNOWN", "unknown", "irreversible")
    assert {describe({"status": s})["group"] for s in ("failed", "rejected", "expired", "cancelled")} == {"completed"}
