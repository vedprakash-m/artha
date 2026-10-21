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
            "known_at_requested_time": bool(selected) if as_of else True,
            "status": selected["status"] if selected else fact.status,
            "provenance": selected["provenance_class"] if selected else fact.provenance_class,
            "observed_at": fact.observed_at,
            "valid_from": selected["valid_from"] if selected else fact.valid_from,
            "valid_to": selected["valid_to"] if selected else fact.valid_to,
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


def precision(ledger: Ledger, viewer: str | None = None) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in ledger.conn.execute("SELECT * FROM feedback"):
        if _feedback_visible(ledger, viewer or ledger._owner, row["obligation_key"]):
            key = (row["response"], row["review_status"])
            counts[key] = counts.get(key, 0) + 1
    return [{"response": response, "review_status": review, "count": count}
            for (response, review), count in sorted(counts.items())]


def _feedback_visible(ledger: Ledger, viewer: str, obligation: str) -> bool:
    refs = ledger.conn.execute("SELECT af.fact_id FROM alert_facts af JOIN alerts a "
                               "ON a.alert_id=af.alert_id WHERE a.obligation_key=?", (obligation,)).fetchall()
    if not refs:
        return False
    try:
        for ref in refs:
            ledger.get_fact(ref[0], viewer)
    except PermissionDenied:
        return False
    return True


def review_feedback(ledger: Ledger, viewer: str, directory: Path, feedback_id: str | None,
                     label: str | None, now: datetime) -> list[dict[str, Any]]:
    """Reviewed correctness is separate from usefulness, timing and already-handled feedback."""
    if label and (not feedback_id or label not in ("correct", "incorrect", "handled", "timing")):
        raise ValueError("Review needs a feedback ID and a supported correctness label")
    output = []
    rows = (ledger.conn.execute("SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)).fetchall()
            if feedback_id else ledger.conn.execute("SELECT * FROM feedback ORDER BY recorded_at LIMIT 50").fetchall())
    for row in rows:
        alert = ledger.get_alert(row["obligation_key"])
        if alert is None:
            continue
        try:
            refs = ledger.conn.execute("SELECT fact_id FROM alert_facts WHERE alert_id=?",
                                       (alert["alert_id"],)).fetchall()
            if not refs:
                continue
            for ref in refs:
                ledger.get_fact(ref[0], viewer)
        except PermissionDenied:
            continue
        if label:
            path = directory / f"{row['feedback_id']}.ae1"
            if ledger._cipher is None or not path.exists():
                raise ValueError("Saved input bundle is missing; this feedback cannot become an evaluation case")
            aad = f"replay:{row['feedback_id']}".encode()
            case = json.loads(ledger._cipher.decrypt(path.read_bytes(), aad=aad))
            case.update(expected_outcome=label, reviewed_at=now.isoformat(), reviewed_by=viewer)
            atomic_bytes(path, ledger._cipher.encrypt(json.dumps(case).encode(), aad=aad)[0])
            with ledger.transaction() as db:
                db.execute("UPDATE feedback SET review_status=? WHERE feedback_id=?", (label, row["feedback_id"]))
            ledger.record_capability("RA-22", row["feedback_id"])
        if label or row["review_status"] == "unreviewed":
            output.append({"id": row["feedback_id"], "response": row["response"],
                           "status": label or row["review_status"], "domain": alert["domain"]})
    if feedback_id and not output:
        raise ValueError("No accessible feedback matches that ID")
    return output


def class_precision(ledger: Ledger, viewer: str | None = None) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in ledger.conn.execute("SELECT a.domain,a.severity,fb.review_status,fb.obligation_key "
            "FROM feedback fb JOIN alerts a ON a.obligation_key=fb.obligation_key "
            "WHERE fb.review_status IN ('correct','incorrect')"):
        if _feedback_visible(ledger, viewer or ledger._owner, row["obligation_key"]):
            key = (row["domain"], row["severity"], row["review_status"])
            counts[key] = counts.get(key, 0) + 1
    return [{"domain": domain, "severity": severity, "review_status": review, "count": count}
            for (domain, severity, review), count in sorted(counts.items())]


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
    ledger.correction_notice(fact_id, "Explicit user correction; the archived Briefing preserves its original text.")
    ledger.record_capability("RA-06", fact_id + ":" + expected_recorded_at)


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


def render(command: str, result: Mapping[str, Any], *, detail: bool = False) -> str:
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
            value = row.get("value", row.get("status", ""))
            prefix = f"{identity}: " if command != "beliefs" or detail else ""
            output.append(sanitize(f"{prefix}{label.replace('_', ' ')} — {value}", 500))
        if command == "goals":
            output.extend(sanitize(str(line), 500) for line in result.get("observations", []))
        return "\n".join(output)
    import json  # noqa: PLC0415
    # Key/value detail retains unknowns and receipts without implying extra facts.
    return "\n".join(f"{key.replace('_', ' ')}: {sanitize(json.dumps(value, default=str), 4000)}"
                     for key, value in result.items())
