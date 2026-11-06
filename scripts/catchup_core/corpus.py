"""Labeled evaluation corpus (re-artha §11.1): pre-fill cases from a shadow run, record the owner's labels, and report
what the release scorecard still lacks.

Every case and label is stored encrypted under the ledger's data key (they quote real mail); nothing here reaches a
model or the network.  A label is only ever recorded from an explicit decision; "unsure" is kept but never counts.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from catchup_core.backup import atomic_bytes
from catchup_core.crypto import DecryptionError
from catchup_core.ledger import PermissionDenied

SEVERITIES = ("P1", "P2")
CASE_TYPES = ("positive", "negative", "cross_domain", "goal_correction")
FIELD_KINDS = ("date", "amount", "name")
_URL = re.compile(r"https?://\S+|\[[^\]]*\]\([^)]*\)|<!--.*?-->", re.S)
EXCERPT_CHARS = 400


class CorpusError(ValueError):
    pass


def _dir(ledger: Any) -> Path:
    return Path(ledger._path.parent) / "corpus"


def _excerpt(text: str) -> str:
    plain = re.sub(r"\s+", " ", _URL.sub("", text)).strip()
    return plain[:EXCERPT_CHARS]


def prefill(ledger: Any, text_of: Any, connector: str, domain: str) -> list[dict[str, Any]]:
    """One unlabeled case per fetched Signal of ``connector``, showing what the core surfaced from it."""
    cases = []
    rows = ledger.conn.execute(
        "SELECT signal_id, upstream_id, observed_at, evidence_blob_ref FROM signals WHERE connector=? "
        "ORDER BY observed_at, signal_id", (connector,)).fetchall()
    outcome = latest_outcome(ledger)
    for row in rows:
        try:
            ledger.require_signal_operation(row["signal_id"], ledger._owner)
        except PermissionDenied:
            continue
        surfaced = sorted(a["obligation_key"] for a in (outcome or {}).get("alerts", [])
                          if row["signal_id"] in a["signals"])
        text = text_of(row["evidence_blob_ref"])
        case_id = "case_" + str(row["signal_id"]).removeprefix("sig_")[:16]
        cases.append({
            "case_id": case_id, "domain": domain,
            "signal_id": row["signal_id"], "observed_at": row["observed_at"],
            "title": _excerpt(text.splitlines()[0] if text.strip() else "")[:90], "excerpt": _excerpt(text),
            "core_surfaced": surfaced, "labeled": labeled(ledger, case_id),
            "evaluation_catchup": outcome.get("catchup_id") if outcome else None,
            "blob_ref": row["evidence_blob_ref"],
        })
    return cases


def _case_id(case_id: Any) -> str:
    if not isinstance(case_id, str) or not re.fullmatch(r"case_[A-Za-z0-9_-]{1,64}", case_id):
        raise CorpusError("invalid corpus identity")
    return case_id


def labeled(ledger: Any, case_id: str) -> bool:
    _case_id(case_id)
    return (_dir(ledger) / f"{case_id}.ae1").is_file()


def _bounded(value: Any, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _fields(raw: Any) -> list[dict[str, str]]:
    """Owner-asserted field truth; each value is tied to a verbatim span of the source message."""
    if not isinstance(raw, list | tuple) or len(raw) > 200:
        raise CorpusError("fields must be a bounded list")
    out = []
    for item in raw:
        if (not isinstance(item, Mapping) or item.get("kind") not in FIELD_KINDS
                or not _bounded(item.get("predicate"), 128) or not _bounded(item.get("value"), 512)
                or not _bounded(item.get("span"), 512)):
            raise CorpusError("each field needs a kind, predicate, value and verbatim source span")
        out.append({key: str(item[key]) for key in ("kind", "predicate", "value", "span")})
    return out


def _threads(raw: Any) -> list[str]:
    if (not isinstance(raw, list | tuple) or len(raw) > 100 or any(not _bounded(t, 1024) for t in raw)
            or len(set(raw)) != len(raw)):
        raise CorpusError("thread ids must be a bounded list of distinct nonempty strings")
    return sorted(raw)


def blind_case(case: Mapping[str, Any], text: str) -> dict[str, Any]:
    """What the labeler may see: the source message only, never the core's output (HL-01)."""
    return {"case_id": case["case_id"], "domain": case["domain"], "observed_at": case["observed_at"], "text": text}


def validate(label: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(label, Mapping):
        raise CorpusError("corpus label must be an object")
    needs = label.get("needs_alert")
    if needs not in ("yes", "no", "unsure"):
        raise CorpusError("needs_alert must be yes, no or unsure")
    case_type = label.get("case_type") or ("negative" if needs == "no" else "positive")
    if case_type not in CASE_TYPES:
        raise CorpusError(f"case_type must be one of {CASE_TYPES}")
    raw_expected = label.get("expected", [])
    if (not isinstance(raw_expected, list | tuple) or len(raw_expected) > 100
            or any(not isinstance(key, str) or not key.strip() or len(key) > 512 for key in raw_expected)):
        raise CorpusError("expected obligations must be a bounded list of nonempty keys")
    if len(set(raw_expected)) != len(raw_expected):
        raise CorpusError("expected obligations must be distinct")
    expected = sorted(raw_expected)
    if needs == "yes":
        if label.get("severity") not in SEVERITIES:
            raise CorpusError("a case that needed an Alert needs severity P1 or P2")
        if not expected:
            raise CorpusError("a case that needed an Alert needs the obligation key(s) it should have surfaced")
        if case_type == "negative":
            raise CorpusError("a negative case cannot expect an Alert")
    elif needs == "no" and (expected or case_type != "negative"):
        raise CorpusError("a case that needed no Alert is negative and expects nothing")
    truth, threads = _fields(label.get("fields", [])), _threads(label.get("thread_ids", []))
    transition = {}
    if case_type == "goal_correction" and needs == "yes":
        before, after = label.get("before"), label.get("after")
        if (before is None or after is None or before == after
                or len(json.dumps([before, after])) > 4000):
            raise CorpusError("Goal correction needs distinct before and after observations")
        transition = {"before": before, "after": after}
    return {**transition, "needs_alert": needs, "case_type": case_type,
            "severity": label.get("severity") if needs == "yes" else None,
            "expected": expected if needs == "yes" else [], "note": str(label.get("note", ""))[:500],
            "fields": truth, "thread_ids": threads}


def record_label(ledger: Any, case: Mapping[str, Any], label: Mapping[str, Any], now: datetime,
                 source_text: str | None = None) -> dict[str, Any]:
    ledger.require_signal_operation(str(case["signal_id"]), ledger._owner)
    _case_id(case["case_id"])
    checked = validate(label)
    if checked["fields"]:
        if source_text is None:
            raise CorpusError("field truth needs the source text to verify each span")
        if any(item["span"] not in source_text for item in checked["fields"]):
            raise CorpusError("each field span must appear verbatim in the source message")
    reviewed = checked["needs_alert"] != "unsure"
    stored = {
        **checked, "case_id": case["case_id"], "domain": case["domain"], "signal_id": case["signal_id"],
        "schema": "artha.corpus.label.v3", "reviewed": reviewed, "observed_at": case.get("observed_at"),
        "source_negative_reviewed": reviewed and checked["needs_alert"] == "no",
        "labeled_at": now.isoformat(), "excerpt": case.get("excerpt", ""),
    }
    if ledger._cipher is None:
        raise CorpusError("the corpus is stored encrypted; no key available")
    path = _dir(ledger) / f"{case['case_id']}.ae1"
    if path.is_file():             # an earlier decision is kept, append-only; only the latest counts (HL-10)
        prior = json.loads(ledger._cipher.decrypt(path.read_bytes(), aad=case["case_id"].encode()))
        stored["history"] = [*prior.pop("history", []), prior]
    with ledger.transaction() as db:
        prior = db.execute("SELECT signal_id FROM corpus_cases WHERE case_id=?", (case["case_id"],)).fetchone()
        if prior and prior[0] != case["signal_id"]:
            raise CorpusError("corpus identity belongs to another source")
        db.execute("INSERT OR IGNORE INTO corpus_cases VALUES(?,?)", (case["case_id"], case["signal_id"]))
    atomic_bytes(path, ledger._cipher.encrypt(json.dumps(stored).encode(), aad=case["case_id"].encode())[0])
    return stored


def load_labels(ledger: Any) -> list[dict[str, Any]]:
    out = []
    for path in sorted(_dir(ledger).glob("case_*.ae1")):
        try:
            label = json.loads(ledger._cipher.decrypt(path.read_bytes(), aad=path.stem.encode()))
            _case_id(path.stem)
            if not isinstance(label, dict):
                raise CorpusError("corpus label must be an object")
            if label.get("case_id") != path.stem:
                raise CorpusError("corpus identity does not match its envelope")
            if not isinstance(label.get("signal_id"), str):
                raise CorpusError("corpus source identity must be a string")
            try:
                ledger.require_signal_operation(label["signal_id"], ledger._owner)
            except PermissionDenied:
                continue
            source = ledger.conn.execute("SELECT signal_id FROM corpus_cases WHERE case_id=?",
                                         (path.stem,)).fetchone()
            if source is None or source[0] != label["signal_id"]:
                raise CorpusError("corpus identity does not match its registered source")
            checked = validate(label)
            reviewed = checked["needs_alert"] != "unsure"
            negative = reviewed and checked["needs_alert"] == "no"
            if (type(label.get("reviewed")) is not bool or label["reviewed"] != reviewed
                    or type(label.get("source_negative_reviewed")) is not bool
                    or label["source_negative_reviewed"] != negative):
                raise CorpusError("corpus review flags do not match the recorded decision")
            # v1 files may contain development-era surfaced values; they never establish a new outcome.
            label.pop("surfaced", None)
            # Earlier writers asserted this without recording any live-source attestation.
            label.pop("consented_live", None)
            out.append(label)
        except CorpusError:
            raise
        except (OSError, ValueError, KeyError, TypeError, DecryptionError) as exc:
            raise CorpusError(f"{path.name} cannot be decrypted") from exc
    return out


def record_outcome(ledger: Any, catchup_id: str, payload: Mapping[str, Any], now: datetime) -> None:
    """Freeze the returned Briefing's actual output, without turning a label into implementation evidence."""
    if ledger._cipher is None:
        raise CorpusError("evaluation outcomes require encryption")
    encrypted, version = ledger._cipher.encrypt_json(dict(payload), aad=f"evaluation:{catchup_id}".encode())
    with ledger.transaction() as db:
        db.execute("INSERT OR IGNORE INTO catchup_evaluations VALUES(?,?,?,?)",
                   (catchup_id, encrypted, version, now.isoformat()))


def latest_outcome(ledger: Any, catchup_id: str | None = None) -> dict[str, Any] | None:
    query = "SELECT e.* FROM catchup_evaluations e JOIN stage_journal j ON j.catchup_id=e.catchup_id " \
            "AND j.stage='PRESENTED' AND j.status='committed'"
    row = ledger.conn.execute(query + (" WHERE e.catchup_id=?" if catchup_id else "")
        + " ORDER BY e.created_at DESC,e.catchup_id DESC LIMIT 1", (catchup_id,) if catchup_id else ()).fetchone()
    if row is None:
        return None
    outcome = dict(ledger._cipher.decrypt_json(row["payload"], aad=f"evaluation:{row['catchup_id']}".encode()))
    provider_generations = outcome.get("provider_signal_generations", {})
    outcome_generation = outcome.get("permission_generation")
    if (not isinstance(provider_generations, Mapping)
            or (outcome_generation is not None
                and (type(outcome_generation) is not int or outcome_generation < 0))
            or any(not isinstance(sid, str) or type(generation) is not int or generation < 0
                   for sid, generation in provider_generations.items())
            or not set(provider_generations) <= set(outcome.get("signals", ()))):
        raise CorpusError("invalid provider provenance in retained outcome")
    for signal_id in outcome["signals"]:
        ledger.require_signal_operation(signal_id, ledger._owner)
    for alert in outcome["alerts"]:
        for signal_id in alert["signals"]:
            ledger.require_signal_operation(signal_id, ledger._owner)
    current_generation = ledger._permission_generation()
    outcome["consented_live_signal_ids"] = sorted(
        signal_id for signal_id, generation in provider_generations.items()
        if generation == outcome_generation == current_generation)
    return outcome


def evaluations(labels: Sequence[Mapping[str, Any]], outcome: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Join reviewed labels to one explicit frozen Catch-up; historic stored 'surfaced' fields are ignored."""
    if outcome is None:
        return []
    output = []
    for label in labels:
        if not label.get("reviewed") or label.get("signal_id") not in outcome["signals"]:
            continue
        surfaced = sorted(a["obligation_key"] for a in outcome["alerts"] if label["signal_id"] in a["signals"])
        output.append({**label, "surfaced": surfaced, "catchup_id": outcome["catchup_id"],
                       "consented_live": label["signal_id"] in outcome.get("consented_live_signal_ids", ()),
                       "cohort": outcome.get('cohort'),
                       "versions": outcome["versions"], "coverage_complete": outcome["coverage_complete"],
                       "quiet_claim": outcome["quiet_claim"], "citations_resolve": outcome["citations_resolve"]})
    return output


def gaps(labels: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """What the release scorecard still lacks, in the gate's own terms (policy._validate_cohorts)."""
    reviewed = [dict(label) for label in labels if label.get("reviewed")]
    positive = {s: sum(1 for c in reviewed if c.get("severity") == s and c.get("expected")) for s in SEVERITIES}
    negatives = sum(1 for c in reviewed if c["case_type"] == "negative" and c.get("source_negative_reviewed"))
    cross = sum(1 for c in reviewed if c["case_type"] == "cross_domain" and c.get("expected"))
    goal = sum(1 for c in reviewed if c["case_type"] == "goal_correction" and c.get("expected")
               and c.get("before") is not None and c.get("after") is not None and c["before"] != c["after"])
    missing = []
    missing += [f"reviewed positive {s} outcome" for s in SEVERITIES if not positive[s]]
    missing += ["reviewed source-negative outcome"] if not negatives else []
    missing += [f"{2 - cross} more cross-Domain outcome(s)"] if cross < 2 else []
    missing += ["correction-sensitive Goal outcome"] if not goal else []
    return {"labeled": len(labels), "reviewed": len(reviewed), "unsure": len(labels) - len(reviewed),
            "positive": positive, "source_negative": negatives, "cross_domain": cross, "goal_correction": goal,
            "missing_for_gate": missing}
