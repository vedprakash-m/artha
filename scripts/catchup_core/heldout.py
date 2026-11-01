"""Held-out evaluation evidence (specs/features/heldout-labeling.md): declaration, field scoring and assembly.

Everything is encrypted under the ledger's data key and stays local.  Assembly only restates the owner's reviewed labels
in the shapes the unchanged release validators read; a shortfall is a refusal that names what is missing.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from catchup_core import corpus
from catchup_core.backup import atomic_bytes
from catchup_core.corpus import CorpusError
from catchup_core.ledger import LedgerError, PermissionDenied

MIN_FIELDS = 100                      # owner-approved target per field type (D-3); 300+ preferred
BASIS = "blind-owner"
BASIS_STATEMENT = ("labeled by the owner from the source message alone, before any core output was shown; "
                   "this shows blindness, not a second person's independence")
_NAMES = {"date": "dates", "amount": "amounts", "name": "names"}
_VERIFIED = {"verified_source", "user_confirmed"}
_NOT_NUMERIC = re.compile(r"[^0-9.\-]")


def _aware(value: Any, what: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise CorpusError(f"{what} must be an ISO timestamp with a timezone") from None
    if stamp.tzinfo is None:
        raise CorpusError(f"{what} needs a timezone")
    return stamp


def _path(ledger: Any) -> Any:
    return corpus._dir(ledger) / "declaration.ae1"


def declare(ledger: Any, *, corpus_id: str, development_end_at: str, development_obligation_ids: Sequence[str],
            development_thread_ids: Sequence[str], now: datetime) -> dict[str, Any]:
    if not corpus._bounded(corpus_id, 256):
        raise CorpusError("corpus id must be a nonempty string")
    _aware(development_end_at, "development end")
    if ledger._cipher is None:
        raise CorpusError("the corpus is stored encrypted; no key available")
    path = _path(ledger)
    if path.is_file():
        raise CorpusError("a corpus declaration already exists; the split cannot be changed")
    declaration = {
        "schema": "artha.corpus.declaration.v1", "corpus_id": corpus_id, "development_end_at": development_end_at,
        "development_obligation_ids": sorted(set(development_obligation_ids)),
        "development_thread_ids": sorted(set(development_thread_ids)), "declared_at": now.isoformat(),
        "declared_by": ledger._owner, "independence_basis": BASIS}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, ledger._cipher.encrypt(json.dumps(declaration).encode(), aad=b"declaration")[0])
    return declaration


def load_declaration(ledger: Any) -> dict[str, Any] | None:
    path = _path(ledger)
    if not path.is_file():
        return None
    try:
        value = json.loads(ledger._cipher.decrypt(path.read_bytes(), aad=b"declaration"))
    except Exception as exc:
        raise CorpusError("the corpus declaration cannot be decrypted") from exc
    if not isinstance(value, dict) or value.get("schema") != "artha.corpus.declaration.v1":
        raise CorpusError("the corpus declaration is malformed")
    return value


def normalize(kind: str, value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = text.strip()
    if kind == "date":
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return text
    if kind == "amount":
        try:
            return f"{Decimal(_NOT_NUMERIC.sub('', text)):.2f}"
        except InvalidOperation:
            return text
    return re.sub(r"\s+", " ", text).casefold()


def _truth(labels: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, str]]]:
    return [(label, item) for label in labels if label.get("reviewed") for item in label.get("fields", [])]


def _facts(ledger: Any, signal_id: str, viewer: str) -> list[Any]:
    out = []
    for row in ledger.conn.execute("SELECT DISTINCT fact_id FROM fact_evidence WHERE signal_id=?", (signal_id,)):
        try:
            fact = ledger.get_fact(row[0], viewer)
        except (PermissionDenied, LedgerError):
            continue
        if fact.status == "current":
            out.append(fact)
    return out


def score_fields(ledger: Any, labels: Sequence[Mapping[str, Any]], viewer: str) -> dict[str, dict[str, int]]:
    counts = {name: {"correct": 0, "total": 0} for name in _NAMES.values()}
    false = {"false": 0, "total": 0}
    for label, item in _truth(labels):
        kind, expected = item["kind"], normalize(item["kind"], item["value"])
        matching = [f for f in _facts(ledger, label["signal_id"], viewer) if f.predicate == item["predicate"]]
        row = counts[_NAMES[kind]]
        row["total"] += 1
        row["correct"] += any(normalize(kind, f.value) == expected for f in matching)
        for fact in matching:
            if fact.provenance_class in _VERIFIED:
                false["total"] += 1
                false["false"] += normalize(kind, fact.value) != expected
    return {**counts, "false_confirmations": false}


def _field_counts(labels: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(_NAMES.values(), 0)
    for _label, item in _truth(labels):
        counts[_NAMES[item["kind"]]] += 1
    return counts


def status(ledger: Any, labels: Sequence[Mapping[str, Any]], *, min_fields: int = MIN_FIELDS) -> dict[str, Any]:
    declared = load_declaration(ledger) is not None
    counts = _field_counts(labels)
    short = {name: min_fields - n for name, n in counts.items() if n < min_fields}
    missing = list(corpus.gaps(labels)["missing_for_gate"])
    missing += [] if declared else ["corpus declaration (id, development cutoff and development ids)"]
    missing += [f"{n} more {name} field(s)" for name, n in short.items()]
    domains: dict[str, int] = {}
    for label in labels:
        if label.get("reviewed"):
            domains[label["domain"]] = domains.get(label["domain"], 0) + 1
    return {**corpus.gaps(labels), "declared": declared, "fields": counts, "fields_missing": short,
            "by_domain": domains, "missing_for_gate": missing}


def assemble(ledger: Any, labels: Sequence[Mapping[str, Any]], outcome: Mapping[str, Any], *, as_of: str,
             min_fields: int = MIN_FIELDS) -> dict[str, Any]:
    declaration = load_declaration(ledger)
    if declaration is None:
        raise CorpusError("assembly needs a corpus declaration")
    _aware(as_of, "as-of time")
    cutoff = _aware(declaration["development_end_at"], "development end")
    held = [dict(label) for label in labels if label.get("reviewed")
            and label.get("observed_at") and _aware(label["observed_at"], "case date") > cutoff]
    if not held:
        raise CorpusError("no held-out cases after the development cutoff")
    bad = sorted(label["case_id"] for label in held if not label.get("thread_ids"))
    if bad:
        raise CorpusError(f"cases lack thread ids: {', '.join(bad)}")
    leaks = sorted(label["case_id"] for label in held
                   if set(label["thread_ids"]) & set(declaration["development_thread_ids"])
                   or set(label.get("expected", [])) & set(declaration["development_obligation_ids"]))
    if leaks:
        raise CorpusError(f"cases overlap the development set: {', '.join(leaks)}")
    uncovered = sorted(label["case_id"] for label in held if label["signal_id"] not in outcome["signals"])
    if uncovered:
        raise CorpusError(f"cases are not covered by this Catch-up: {', '.join(uncovered)}")
    scores = score_fields(ledger, held, ledger._owner)
    short = [f"{name} {scores[name]['total']}/{min_fields}" for name in _NAMES.values()
             if scores[name]["total"] < min_fields]
    if short:
        raise CorpusError("field truth is below the approved floor: " + ", ".join(short))
    evaluations = []
    for item in corpus.evaluations(held, outcome):
        item.update(split="held_out", corpus_id=declaration["corpus_id"], obligation_ids=list(item["expected"]),
                    as_of=as_of, latest_evidence_at=item["observed_at"])
        evaluations.append(item)
    history = hashlib.sha256(json.dumps([[x["case_id"], x.get("history", [])] for x in held],
                                        sort_keys=True).encode()).hexdigest()
    cohort = outcome.get("cohort")
    return {
        "cohort": cohort, "evaluations": evaluations,
        "corpus": {"schema": "artha.release-corpus.v1", "held_out": True, "independently_labeled": True,
                   "id": declaration["corpus_id"], "cohort": cohort,
                   "development_obligation_ids": declaration["development_obligation_ids"],
                   "development_thread_ids": declaration["development_thread_ids"],
                   "development_end_at": declaration["development_end_at"], "basis": declaration["independence_basis"],
                   "basis_statement": BASIS_STATEMENT, "labeled_by": ledger._owner,
                   "assembled_at": datetime.now(UTC).isoformat(), "label_history_sha256": history},
        "document_fields": {**scores, "cohort": cohort, "corpus_id": declaration["corpus_id"]}}
