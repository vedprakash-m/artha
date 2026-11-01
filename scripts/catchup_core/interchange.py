"""Permission-scoped portable interchange and the custodian's emergency view.

Interchange carries only what the exporting person may export, as Facts with their corrections and unresolved
states. It never carries consent, actions, credentials or keys, and reading a file never writes or executes
anything: a recipient's own consent and approvals still apply."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from catchup_core.backup import atomic_bytes
from catchup_core.crypto import EnvelopeCipher
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied

SCHEMA = "artha.interchange.v1"
EXCLUDED = ("consent", "actions", "credentials", "keys")
_AAD = SCHEMA.encode()
_EMERGENCY_STATUSES = ("current", "disputed", "unknown")


def build_interchange(ledger: Ledger, viewer: str) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    omitted = 0
    for row in ledger.conn.execute("SELECT fact_id FROM facts ORDER BY fact_id").fetchall():
        fact_id = row[0]
        try:
            ledger.get_fact_metadata(fact_id, viewer, operation="export")
            fact = ledger.get_fact(fact_id, viewer)
            history = ledger.fact_history(fact_id, viewer)
        except (PermissionDenied, LedgerError):
            omitted += 1
            continue
        co_subjects = sorted(str(r[0]) for r in ledger.conn.execute(
            "SELECT subject_id FROM fact_subjects WHERE fact_id=?", (fact_id,)))
        facts.append({
            "fact_id": fact_id, "domain": fact.domain, "subject": fact.primary_subject_id,
            "co_subjects": co_subjects, "predicate": fact.predicate, "value": fact.value, "status": fact.status,
            "provenance": fact.provenance_class, "observed_at": fact.observed_at, "recorded_at": fact.recorded_at,
            "valid_from": fact.valid_from, "valid_to": fact.valid_to, "validity_unknown": fact.validity_unknown,
            "obligation_key": fact.obligation_key, "evidence": [list(span) for span in fact.evidence],
            "versions": [{"recorded_at": v["recorded_at"], "status": v["status"], "value": v["value"],
                          "provenance_class": v["provenance_class"], "observed_at": v["observed_at"]}
                         for v in history]})
    return {"schema": SCHEMA, "exported_by": viewer, "facts": facts, "omitted_facts": omitted,
            "excluded": list(EXCLUDED), "dispatchable": False}


def write_interchange(path: Path, ledger: Ledger, viewer: str) -> dict[str, Any]:
    cipher = ledger._cipher
    if cipher is None:
        raise LedgerError("interchange requires the encryption key")
    document = build_interchange(ledger, viewer)
    atomic_bytes(path, cipher.encrypt(json.dumps(document).encode(), aad=_AAD)[0])
    return {"exported_facts": len(document["facts"]), "omitted_facts": document["omitted_facts"],
            "format": SCHEMA, "dispatchable": False}


def read_interchange(path: Path, cipher: EnvelopeCipher) -> dict[str, Any]:
    """Decrypt and validate only; the caller receives data to review and nothing is applied."""
    try:
        document = json.loads(cipher.decrypt(Path(path).read_bytes(), aad=_AAD))
    except Exception as exc:
        raise ValueError("not a readable interchange file for this installation") from exc
    if (not isinstance(document, dict) or document.get("schema") != SCHEMA or document.get("dispatchable") is not False
            or not isinstance(document.get("facts"), list) or any(name in document for name in EXCLUDED)):
        raise ValueError("interchange file has an unknown schema or carries authority it must not carry")
    return document


def emergency_view(ledger: Ledger, viewer: str) -> dict[str, Any]:
    """Facts a designated custodian may see: each adult subject granted this person 'emergency' (and view) access
    for that Domain and its sources, and the person is a listed viewer. Anything else is simply absent."""
    from catchup_core.fact_access import AccessPolicy, require_metadata  # noqa: PLC0415
    policy = AccessPolicy(ledger._owner, ledger._consent_subjects, ledger._known_subjects)
    marks = ",".join("?" for _ in _EMERGENCY_STATUSES)
    rows = ledger.conn.execute(  # `marks` is only "?" placeholders; values are bound
        f"SELECT fact_id FROM facts WHERE status IN ({marks}) ORDER BY domain,predicate,fact_id",  # noqa: S608
        _EMERGENCY_STATUSES).fetchall()
    facts: list[dict[str, Any]] = []
    for row in rows:
        try:
            require_metadata(ledger.conn, policy, row[0], viewer, ledger._clock(), operation="emergency",
                             source_operation=True)
            fact = ledger.get_fact(row[0], viewer)
        except (PermissionDenied, LedgerError):
            continue
        facts.append({"fact_id": fact.fact_id, "domain": fact.domain, "subject": fact.primary_subject_id,
                      "predicate": fact.predicate, "value": fact.value, "status": fact.status,
                      "observed_at": fact.observed_at})
    return {"facts": facts, "dispatchable": False}
