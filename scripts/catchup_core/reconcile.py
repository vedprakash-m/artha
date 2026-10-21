"""Entity/time reconciliation of candidate facts (re-artha §7.3, RA-02).

Candidates are grouped by (subject, predicate).  Against what the ledger already
believes, each group is resolved by **per-predicate source authority** from the
Domain pack — not by recency:

=============================================  =====================================
new vs existing                                outcome
=============================================  =====================================
no existing Fact                               new ``current`` Fact
same value                                     corroborate (evidence added)
different value, same upstream message         supersede (the source corrected itself)
different value, strictly higher authority     supersede
different value, equal or lower authority      **dispute** both — never "latest wins"
=============================================  =====================================

A source class absent from the predicate's authority list ranks below every listed
class, so it can corroborate but never overturn or establish a high-stakes value.
"""
from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from catchup_core.contracts import Confidentiality, FactStatus, Provenance
from catchup_core.domain_pack import DomainPack
from catchup_core.extract import Candidate
from catchup_core.ledger import FactInput, FactView, Ledger

__all__ = ["FactChange", "ReconcileResult", "authority_rank", "reconcile"]


@dataclass(frozen=True)
class FactChange:
    kind: str                    # created | corroborated | superseded | disputed | resolved
    fact_id: str
    predicate: str
    subject_id: str
    detail: str = ""


@dataclass(frozen=True)
class ReconcileResult:
    changes: tuple[FactChange, ...]

    def by_kind(self, kind: str) -> list[FactChange]:
        return [c for c in self.changes if c.kind == kind]


def authority_rank(pack: DomainPack, predicate: str, source_class: str) -> int:
    """Lower is stronger.  Unlisted classes rank below every listed class."""
    authority = pack.predicates[predicate].authority
    return authority.index(source_class) if source_class in authority else len(authority)


def _plain(value: Any) -> Any:
    """Unwrap a legacy-import envelope so it compares with extracted values."""
    if isinstance(value, dict) and set(value) == {"value", "lineage"}:
        return value["value"]
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _existing_rank(ledger: Ledger, pack: DomainPack, fact: FactView,
                   source_class_of: Callable[[str], str]) -> int:
    ranks = [authority_rank(pack, fact.predicate, source_class_of(sig)) for sig, _s, _e in fact.evidence]
    if fact.provenance_class == Provenance.USER_CONFIRMED.value:
        return -1                                   # a user's own confirmation outranks any source
    if fact.provenance_class == Provenance.LEGACY_UNVERIFIED.value:
        return len(pack.predicates[fact.predicate].authority) + 1
    return min(ranks) if ranks else len(pack.predicates[fact.predicate].authority)


def _fact_input(pack: DomainPack, predicate: str, subject: str, confidentiality: Confidentiality, value: Any,
                evidence: tuple[tuple[str, int, int], ...], observed_at: str, rule_version: str | None,
                status: FactStatus, prov: Provenance, obligation_key: str = "") -> FactInput:
    return FactInput(
        domain=pack.predicate_domains.get(predicate, pack.domain), predicate=predicate, value=value,
        primary_subject_id=subject, observed_at=observed_at,
        confidentiality=confidentiality, provenance=prov, status=status, rule_version=rule_version,
        evidence=evidence, obligation_key=obligation_key)


def reconcile(
    ledger: Ledger,
    pack: DomainPack,
    candidates: Sequence[Candidate],
    *,
    observed_at: str,
    source_class_of: Callable[[str], str],
    upstream_key_of: Callable[[str], tuple[str, str, str]],
    rule_version: str | None = None,
) -> ReconcileResult:
    """Apply candidates to the ledger and return what changed."""
    changes: list[FactChange] = []
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for cand in candidates:
        groups.setdefault((cand.subject_id, cand.predicate), []).append(cand)

    for (subject, predicate), group in sorted(groups.items()):
        pred = pack.predicates[predicate]
        confidentiality = pred.confidentiality or pack.confidentiality
        for cand in group:
            value = _json_value(cand.value)
            rank = authority_rank(pack, predicate, source_class_of(cand.signal_id))
            domain = pack.predicate_domains.get(predicate, pack.domain)
            existing = ledger.facts_matching(domain, predicate, subject, obligation_key=cand.obligation_key)
            evidence = ((cand.signal_id, cand.span[0], cand.span[1]),)

            make = functools.partial(_fact_input, pack, predicate, subject, confidentiality, value, evidence,
                                     observed_at, rule_version, obligation_key=cand.obligation_key)

            if not existing:
                trusted = rank < len(pred.authority)
                fid = ledger.put_fact(make(status=FactStatus.CURRENT if trusted else FactStatus.UNKNOWN,
                                          prov=Provenance.VERIFIED_SOURCE if trusted else Provenance.MODEL_HYPOTHESIS))
                changes.append(FactChange("created", fid, predicate, subject))
                continue

            same = [f for f in existing if _plain(f.value) == value]
            if same:
                for fact in same:
                    ledger.add_evidence(fact.fact_id, cand.signal_id, cand.span[0], cand.span[1])
                    changes.append(FactChange("corroborated", fact.fact_id, predicate, subject))
                continue

            ranks = {f.fact_id: _existing_rank(ledger, pack, f, source_class_of) for f in existing}
            my_key = upstream_key_of(cand.signal_id)
            same_upstream = [
                f for f in existing
                if any(upstream_key_of(sig) == my_key for sig, _s, _e in f.evidence)
            ]
            confirmed = any(f.provenance_class == Provenance.USER_CONFIRMED.value for f in existing)
            overturn = not confirmed and (bool(same_upstream) or all(rank < r for r in ranks.values()))
            if overturn:
                fid = ledger.put_fact(make(status=FactStatus.CURRENT, prov=Provenance.VERIFIED_SOURCE))
                for old in existing:
                    ledger.set_fact_status(old.fact_id, FactStatus.SUPERSEDED)
                    changes.append(FactChange("superseded", old.fact_id, predicate, subject, f"by {fid}"))
                changes.append(FactChange("created", fid, predicate, subject, "supersedes"))
            else:
                fid = ledger.put_fact(make(status=FactStatus.DISPUTED, prov=Provenance.DISPUTED))
                for old in existing:
                    if old.status != FactStatus.DISPUTED.value:
                        ledger.set_fact_status(old.fact_id, FactStatus.DISPUTED, Provenance.DISPUTED)
                    changes.append(FactChange("disputed", old.fact_id, predicate, subject, f"against {fid}"))
                changes.append(FactChange("disputed", fid, predicate, subject, "new, conflicting source"))
    return ReconcileResult(tuple(changes))


def legacy_import(
    ledger: Ledger,
    pack: DomainPack,
    claims: Mapping[tuple[str, str], Any],
    *,
    lineage: str,
    observed_at: str,
) -> list[str]:
    """Import curated State File claims as ``legacy_unverified`` Facts (§7.4).

    They keep their value, are labelled, rank below every source, and never alone
    authorize a high-stakes action.  ``lineage`` is ``legacy_state:<path>@<content-hash>``.
    """
    created: list[str] = []
    for (subject, predicate), value in sorted(claims.items()):
        if predicate not in pack.predicates:
            continue
        pred = pack.predicates[predicate]
        fid = ledger.put_fact(FactInput(
            domain=pack.domain, predicate=predicate, value={"value": _json_value(value), "lineage": lineage},
            primary_subject_id=subject, observed_at=observed_at,
            confidentiality=pred.confidentiality or pack.confidentiality,
            provenance=Provenance.LEGACY_UNVERIFIED, status=FactStatus.CURRENT))
        created.append(fid)
    return created
