"""Permission-filtered read and feedback surfaces shared by CLI and assistant clients."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from catchup_core import ids
from catchup_core.backup import atomic_bytes
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import FactStatus
from catchup_core.ledger import Ledger, PermissionDenied


def beliefs(ledger: Ledger, viewer: str, *, domain: str | None = None,
            subject: str | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in ledger.conn.execute("SELECT fact_id FROM facts WHERE status IN ('current','disputed','unknown') "
                                   "ORDER BY domain,predicate,recorded_at"):
        try:
            fact = ledger.get_fact(row[0], viewer)
        except PermissionDenied:
            continue
        if (domain and fact.domain != domain) or (subject and fact.primary_subject_id != subject):
            continue
        output.append({"fact_id": fact.fact_id, "domain": fact.domain, "subject": fact.primary_subject_id,
            "predicate": fact.predicate, "value": fact.value, "status": fact.status,
            "provenance": fact.provenance_class, "observed_at": fact.observed_at})
    return output


def explain(ledger: Ledger, blobs: EncryptedBlobStore, viewer: str, fact_id: str,
            *, as_of: str | None = None) -> dict[str, Any]:
    fact = ledger.get_fact(fact_id, viewer)
    history = ledger.fact_history(fact_id, viewer, as_of)
    selected = history[-1] if history else None
    spans = fact.evidence
    if as_of:
        spans = tuple((e["signal_id"], e["span_start"], e["span_end"])
                      for e in (selected or {}).get("evidence", []))
    evidence = []
    for sid, start, end in spans:
        row = ledger.signal_row(sid)
        if row:
            text = blobs.get(row["evidence_blob_ref"])
            evidence.append({"signal_id": sid, "connector": row["connector"], "scope": row["account_scope"],
                             "observed_at": row["observed_at"], "quote": text[start:end]})
    value = (selected["value"] if selected else None) if as_of else fact.value
    return {"fact_id": fact_id, "value": value,
            "known_at_requested_time": bool(selected) if as_of else True, "status": fact.status,
            "provenance": fact.provenance_class, "observed_at": fact.observed_at,
            "valid_from": fact.valid_from, "valid_to": fact.valid_to,
            "evidence": evidence, "history": history}


def feedback(ledger: Ledger, viewer: str, obligation: str, response: str, reason: str,
             now: datetime, *, days: int = 7) -> str:
    if (response not in ("useful", "dismiss", "snooze", "acknowledge") or not reason.strip()
            or not 1 <= days <= 365):
        raise ValueError("feedback needs a supported response and a reason")
    alert = ledger.get_alert(obligation)
    if not alert:
        raise ValueError("unknown Alert")
    refs = ledger.conn.execute("SELECT fact_id FROM alert_facts WHERE alert_id=?", (alert["alert_id"],)).fetchall()
    if not refs:
        raise PermissionDenied("Alert has no accessible evidence")
    for row in refs:
        ledger.get_fact(row[0], viewer)
    fid = ids.new_id("fbk")
    if ledger._cipher is None:
        raise ValueError("feedback storage requires encryption")
    protected_reason = ledger._cipher.encrypt_json(reason, aad=fid.encode())[0]
    with ledger.transaction() as db:
        db.execute("INSERT INTO feedback(feedback_id,obligation_key,response,reason,actor,recorded_at) "
                   "VALUES(?,?,?,?,?,?)", (fid, obligation, response, protected_reason, viewer, now.isoformat()))
        if response in ("dismiss", "snooze"):
            db.execute("UPDATE alerts SET status='snoozed',suppressed_until=?,reopen_condition=? WHERE alert_id=?",
                       ((now + timedelta(days=days)).isoformat(),
                        "evidence changes or snooze expires", alert["alert_id"]))
        elif response == "acknowledge":
            db.execute("UPDATE alerts SET status='acknowledged' WHERE alert_id=?", (alert["alert_id"],))
    return fid


def replay_case(ledger: Ledger, blobs: EncryptedBlobStore, viewer: str, obligation: str, response: str,
                reason: str, feedback_id: str, now: datetime, directory: Path) -> Path:
    """RA-22: save the feedback with its encrypted input bundle as an unreviewed replay case.

    A dismissal may mean handled, wrong timing or not useful, so the expected outcome stays
    ``pending_review`` until the owner labels it; only a reviewed case becomes a gold label.
    """
    cipher = ledger._cipher
    if cipher is None:
        raise ValueError("replay cases require encryption")
    alert = ledger.get_alert(obligation)
    if alert is None:
        raise ValueError("unknown Alert")
    inputs: list[dict[str, Any]] = []
    signal_ids: list[str] = []
    for row in ledger.conn.execute("SELECT fact_id FROM alert_facts WHERE alert_id=?", (alert["alert_id"],)):
        fact = ledger.get_fact(row[0], viewer)
        for sid, _start, _end in fact.evidence:
            signal = ledger.signal_row(sid)
            if signal is not None and sid not in signal_ids:
                signal_ids.append(sid)
                inputs.append({"connector": signal["connector"], "scope": signal["account_scope"],
                               "observed_at": signal["observed_at"], "text": blobs.get(signal["evidence_blob_ref"])})
    bundle = {"schema": "artha.replay.v1", "feedback_id": feedback_id, "obligation": obligation,
              "response": response, "reason": reason, "recorded_at": now.isoformat(),
              "expected_outcome": "pending_review", "inputs": inputs}
    path = directory / f"{feedback_id}.ae1"
    atomic_bytes(path, cipher.encrypt(json.dumps(bundle).encode(), aad=f"replay:{feedback_id}".encode())[0])
    with ledger.transaction() as db:
        for sid in signal_ids:
            db.execute("INSERT OR IGNORE INTO replay_cases(feedback_id,signal_id) VALUES(?,?)", (feedback_id, sid))
    return path


def precision(ledger: Ledger) -> list[dict[str, Any]]:
    return [dict(r) for r in ledger.conn.execute("SELECT response,review_status,count(*) AS count "
                                                 "FROM feedback GROUP BY response,review_status")]


def correct(ledger: Ledger, viewer: str, fact_id: str, value: Any, expected_recorded_at: str) -> None:
    """Explicit user-confirmed correction; the caller has already displayed the exact diff."""
    fact = ledger.get_fact(fact_id, viewer)
    if fact.recorded_at != expected_recorded_at:
        raise ValueError("Fact changed since the correction preview")
    from catchup_core.contracts import Confidentiality, Provenance  # noqa: PLC0415
    from catchup_core.ledger import FactInput  # noqa: PLC0415
    ledger.put_fact(FactInput(domain=fact.domain, predicate=fact.predicate, value=value,
        primary_subject_id=fact.primary_subject_id, observed_at=fact.observed_at,
        confidentiality=Confidentiality(fact.confidentiality), provenance=Provenance.USER_CONFIRMED,
        status=FactStatus.CURRENT, evidence=(), obligation_key=fact.obligation_key), fact_id=fact_id)


def safe_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """A data-only response; frontends must render quotes inertly, never as HTML/links."""
    from catchup_core.compose import sanitize  # noqa: PLC0415
    def inert(item: Any) -> Any:
        if isinstance(item, str):
            return sanitize(item, 4000)
        if isinstance(item, list):
            return [inert(v) for v in item]
        if isinstance(item, dict):
            return {k: inert(v) for k, v in item.items()}
        return item
    return dict(inert(dict(value)))


def render(command: str, result: Mapping[str, Any]) -> str:
    """Plain-text projection for terminal users; structured clients request JSON."""
    from catchup_core.compose import sanitize  # noqa: PLC0415
    rows = result.get(command)
    if isinstance(rows, list):
        if not rows:
            return f"No {command} recorded here."
        output = []
        for row in rows:
            identity = row.get("fact_id") or row.get("id") or row.get("catchup_id", "")
            label = row.get("description") or row.get("title") or row.get("predicate") or row.get("action_type", "")
            detail = row.get("value", row.get("status", ""))
            output.append(sanitize(f"{identity}: {label} — {detail}", 500))
        return "\n".join(output)
    import json  # noqa: PLC0415
    # Key/value detail retains unknowns and receipts without implying extra facts.
    return "\n".join(f"{key.replace('_', ' ')}: {sanitize(json.dumps(value, default=str), 4000)}"
                     for key, value in result.items())
