"""Hardened action lifecycle with an operation registry (re-artha RA-05, §7.6, WP-09).

This is the *target contract* for the action store, expressed against the Appendix A
schema and tested in isolation; wiring it into the existing authoritative
``actions.db`` needs the status mapping of that database and is a separate, gated
change.  Properties:

* approval binds the proposal to its **canonical payload hash**, approver and expiry;
  any payload change invalidates it;
* ``APPROVED → EXECUTING`` is one transaction: reserve the logical **operation key**
  and compare-and-swap the proposal on ``(status, payload_hash, authority_generation)``
  — two sessions approving the same thing dispatch at most once;
* a timed-out or lost provider response is ``UNKNOWN``, never ``FAILED``, and an
  ``UNKNOWN`` operation is not dispatchable until reconciled;
* an outcome and its resulting status commit together;
* restoring a snapshot makes ``APPROVED``/``EXECUTING`` non-dispatchable until reconciled;
* every connection is ``synchronous=FULL``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "ActionError", "ActionStatus", "ActionStore", "ClaimRefused", "OperationState", "canonical_hash",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_proposals(
  proposal_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL, payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL, preconditions_json TEXT NOT NULL, risk_class TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('DRAFT','PROPOSED','APPROVED','EXECUTING','SUCCEEDED',
    'FAILED','UNKNOWN','REJECTED','EXPIRED','DEFERRED','CANCELLED')),
  approved_by TEXT, approved_at TEXT, approval_nonce TEXT, expires_at TEXT NOT NULL,
  authority_generation INTEGER NOT NULL, dispatchable INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS operation_registry(
  operation_key TEXT PRIMARY KEY, owner_proposal_id TEXT NOT NULL, namespace TEXT NOT NULL,
  payload_hash TEXT NOT NULL, first_dispatch_at TEXT,
  state TEXT NOT NULL CHECK(state IN ('reserved','dispatched','unknown','confirmed')), receipt_ref TEXT);
CREATE TABLE IF NOT EXISTS action_outcomes(
  proposal_id TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, provider_request_id TEXT,
  observed_result TEXT NOT NULL, error_class TEXT);
CREATE TABLE IF NOT EXISTS action_transitions(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, payload_hash TEXT);
"""


class ActionError(RuntimeError):
    pass


class ClaimRefused(ActionError):
    """The claim lost (already claimed, payload changed, expired, stale authority, duplicate effect)."""


class ActionStatus(StrEnum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    DEFERRED = "DEFERRED"
    CANCELLED = "CANCELLED"


class OperationState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"
    CONFIRMED = "confirmed"


def canonical_hash(payload: Any) -> str:
    """SHA-256 over the canonical JSON of the exact payload the user reviewed."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class Claim:
    proposal_id: str
    operation_key: str
    payload_hash: str


class ActionStore:
    def __init__(self, conn: sqlite3.Connection, *, authority_generation: int = 1,
                 clock: Callable[[], str] = _now):
        self._conn = conn
        self._gen = authority_generation
        self._clock = clock
        conn.isolation_level = None
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        for statement in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
            conn.execute(statement)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _txn(self) -> _Txn:
        return _Txn(self._conn)

    def _log(self, proposal_id: str, actor: str, old: str | None, new: str, payload_hash: str | None) -> None:
        self._conn.execute(
            "INSERT INTO action_transitions(proposal_id,at,actor,from_status,to_status,payload_hash) "
            "VALUES(?,?,?,?,?,?)", (proposal_id, self._clock(), actor, old, new, payload_hash))

    def _row(self, proposal_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM action_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ActionError(f"unknown proposal {proposal_id}")
        return row

    def status(self, proposal_id: str) -> ActionStatus:
        return ActionStatus(self._row(proposal_id)["status"])

    # ── lifecycle ────────────────────────────────────────────────────────────
    def propose(self, proposal_id: str, *, operation_key: str, payload: Any, expires_at: str,
                risk_class: str = "reversible", preconditions: Any = None, actor: str = "system") -> str:
        """Create a PROPOSED action; returns the payload hash the user must approve."""
        digest = canonical_hash(payload)
        with self._txn():
            self._conn.execute(
                "INSERT INTO action_proposals(proposal_id,operation_key,payload_json,payload_hash,"
                "preconditions_json,risk_class,status,expires_at,authority_generation) "
                "VALUES(?,?,?,?,?,?,'PROPOSED',?,?)",
                (proposal_id, operation_key, json.dumps(payload, sort_keys=True), digest,
                 json.dumps(preconditions or {}, sort_keys=True), risk_class, expires_at, self._gen))
            self._log(proposal_id, actor, None, "PROPOSED", digest)
        return digest

    def approve(self, proposal_id: str, *, payload_hash: str, approver: str, nonce: str) -> None:
        """Approve exactly the reviewed payload.  A different hash is refused."""
        expired = False
        with self._txn():
            row = self._row(proposal_id)
            if row["status"] != "PROPOSED":
                raise ActionError(f"cannot approve a {row['status']} proposal")
            if row["payload_hash"] != payload_hash:
                raise ActionError("approval does not match the current payload (it changed after review)")
            if row["expires_at"] <= self._clock():
                self._conn.execute("UPDATE action_proposals SET status='EXPIRED' WHERE proposal_id=?",
                                   (proposal_id,))
                self._log(proposal_id, "system", "PROPOSED", "EXPIRED", payload_hash)
                expired = True                    # commit the expiry, then refuse
            else:
                self._conn.execute(
                    "UPDATE action_proposals SET status='APPROVED', approved_by=?, approved_at=?, "
                    "approval_nonce=? WHERE proposal_id=? AND status='PROPOSED'",
                    (approver, self._clock(), nonce, proposal_id))
                self._log(proposal_id, approver, "PROPOSED", "APPROVED", payload_hash)
        if expired:
            raise ActionError("proposal expired before approval")

    def revise_payload(self, proposal_id: str, payload: Any, actor: str = "system") -> str:
        """Changing the payload invalidates any approval (back to PROPOSED)."""
        digest = canonical_hash(payload)
        with self._txn():
            row = self._row(proposal_id)
            if row["status"] in ("EXECUTING", "SUCCEEDED", "UNKNOWN"):
                raise ActionError(f"cannot revise a {row['status']} proposal")
            self._conn.execute(
                "UPDATE action_proposals SET payload_json=?, payload_hash=?, status='PROPOSED', approved_by=NULL, "
                "approved_at=NULL, approval_nonce=NULL WHERE proposal_id=?",
                (json.dumps(payload, sort_keys=True), digest, proposal_id))
            self._log(proposal_id, actor, row["status"], "PROPOSED", digest)
        return digest

    def claim(self, proposal_id: str, *, namespace: str = "default") -> Claim:
        """One transaction: reserve the operation key and CAS the proposal to EXECUTING."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._row(proposal_id)
            now = self._clock()
            if not row["dispatchable"]:
                raise ClaimRefused("proposal is not dispatchable until reconciled")
            if row["authority_generation"] != self._gen:
                raise ClaimRefused("authority generation changed; this host may not execute")
            if row["expires_at"] <= now:
                raise ClaimRefused("approval expired")
            existing = self._conn.execute(
                "SELECT * FROM operation_registry WHERE operation_key=?", (row["operation_key"],)).fetchone()
            if existing is not None and existing["owner_proposal_id"] != proposal_id:
                raise ClaimRefused("another proposal already owns this operation")
            if existing is not None and existing["state"] in ("dispatched", "unknown", "confirmed"):
                raise ClaimRefused(f"operation already {existing['state']}")
            cur = self._conn.execute(
                "UPDATE action_proposals SET status='EXECUTING' "
                "WHERE proposal_id=? AND status='APPROVED' AND payload_hash=? AND authority_generation=?",
                (proposal_id, row["payload_hash"], self._gen))
            if cur.rowcount != 1:
                raise ClaimRefused(f"proposal is {row['status']}, not APPROVED")
            self._conn.execute(
                "INSERT INTO operation_registry(operation_key,owner_proposal_id,namespace,payload_hash,state) "
                "VALUES(?,?,?,?,'reserved') ON CONFLICT(operation_key) DO UPDATE SET state='reserved'",
                (row["operation_key"], proposal_id, namespace, row["payload_hash"]))
            self._log(proposal_id, "executor", "APPROVED", "EXECUTING", row["payload_hash"])
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return Claim(proposal_id, row["operation_key"], row["payload_hash"])

    def mark_dispatched(self, claim: Claim) -> None:
        """Called immediately before the provider call; durable (FULL) by the pragma."""
        with self._txn():
            self._conn.execute("UPDATE operation_registry SET state='dispatched', first_dispatch_at=? "
                               "WHERE operation_key=? AND owner_proposal_id=?",
                               (self._clock(), claim.operation_key, claim.proposal_id))

    def record_outcome(self, claim: Claim, *, result: str, provider_request_id: str | None = None,
                       error_class: str | None = None, receipt_ref: str | None = None) -> ActionStatus:
        """``result``: ``success`` | ``failure`` | ``unknown``.  Outcome and status commit together."""
        if result not in ("success", "failure", "unknown"):
            raise ActionError(f"bad result {result!r}")
        status, op_state = {
            "success": (ActionStatus.SUCCEEDED, OperationState.CONFIRMED),
            "failure": (ActionStatus.FAILED, OperationState.RESERVED),
            "unknown": (ActionStatus.UNKNOWN, OperationState.UNKNOWN),
        }[result]
        with self._txn():
            cur = self._conn.execute(
                "UPDATE action_proposals SET status=? WHERE proposal_id=? AND status='EXECUTING'",
                (status.value, claim.proposal_id))
            if cur.rowcount != 1:
                raise ActionError("outcome for a proposal that is not EXECUTING")
            self._conn.execute(
                "INSERT INTO action_outcomes(proposal_id,attempted_at,provider_request_id,observed_result,"
                "error_class) VALUES(?,?,?,?,?)",
                (claim.proposal_id, self._clock(), provider_request_id, result, error_class))
            if result == "failure":
                # a *confirmed* failure releases the key; a later intentional repeat needs a new proposal
                self._conn.execute("DELETE FROM operation_registry WHERE operation_key=?", (claim.operation_key,))
            else:
                self._conn.execute("UPDATE operation_registry SET state=?, receipt_ref=? WHERE operation_key=?",
                                   (op_state.value, receipt_ref, claim.operation_key))
            if result == "unknown":
                self._conn.execute("UPDATE action_proposals SET dispatchable=0 WHERE proposal_id=?",
                                   (claim.proposal_id,))
            self._log(claim.proposal_id, "executor", "EXECUTING", status.value, claim.payload_hash)
        return status

    def reconcile(self, proposal_id: str, check: Callable[[str, str], str | None]) -> ActionStatus:
        """Resolve an UNKNOWN action against the provider.

        ``check(operation_key, payload_hash)`` returns a receipt reference when the effect
        happened, ``None`` when the provider confirms it did not.  Exceptions leave it UNKNOWN.
        """
        row = self._row(proposal_id)
        if row["status"] != "UNKNOWN":
            raise ActionError(f"only UNKNOWN actions are reconciled, this is {row['status']}")
        try:
            receipt = check(row["operation_key"], row["payload_hash"])
        except Exception:
            return ActionStatus.UNKNOWN              # still uncertain; never retried blindly
        with self._txn():
            if receipt is not None:
                self._conn.execute("UPDATE action_proposals SET status='SUCCEEDED' WHERE proposal_id=?",
                                   (proposal_id,))
                self._conn.execute("UPDATE operation_registry SET state='confirmed', receipt_ref=? "
                                   "WHERE operation_key=?", (receipt, row["operation_key"]))
                self._log(proposal_id, "reconciler", "UNKNOWN", "SUCCEEDED", row["payload_hash"])
                return ActionStatus.SUCCEEDED
            self._conn.execute("UPDATE action_proposals SET status='FAILED', dispatchable=1 WHERE proposal_id=?",
                               (proposal_id,))
            self._conn.execute("DELETE FROM operation_registry WHERE operation_key=?", (row["operation_key"],))
            self._log(proposal_id, "reconciler", "UNKNOWN", "FAILED", row["payload_hash"])
            return ActionStatus.FAILED

    def mark_restored(self) -> int:
        """After a restore: nothing in flight is dispatchable until reconciled with the provider."""
        with self._txn():
            cur = self._conn.execute(
                "UPDATE action_proposals SET dispatchable=0 WHERE status IN ('APPROVED','EXECUTING')")
            self._conn.execute("UPDATE action_proposals SET status='UNKNOWN' WHERE status='EXECUTING'")
            return int(cur.rowcount)

    def expire_due(self) -> int:
        with self._txn():
            cur = self._conn.execute(
                "UPDATE action_proposals SET status='EXPIRED' WHERE status IN ('PROPOSED','APPROVED','DEFERRED') "
                "AND expires_at<=?", (self._clock(),))
            return int(cur.rowcount)

    def transitions(self, proposal_id: str) -> list[tuple[str | None, str]]:
        return [(r["from_status"], r["to_status"]) for r in self._conn.execute(
            "SELECT from_status,to_status FROM action_transitions WHERE proposal_id=? ORDER BY seq",
            (proposal_id,))]


class _Txn:
    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def __enter__(self) -> sqlite3.Connection:
        self._c.execute("BEGIN IMMEDIATE")
        return self._c

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._c.execute("ROLLBACK" if exc_type else "COMMIT")
