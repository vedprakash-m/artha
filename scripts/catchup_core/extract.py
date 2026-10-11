"""Typed extraction: model request construction and strict validation (re-artha §6.6, RA-02).

The extractor is a *U-class* call (untrusted content, no tools, no effects, minimum
excerpt).  Its output is data: nothing is persisted until code has checked, per
candidate, that

* the predicate is declared by the Domain pack and the value has that predicate's type,
* the subject is one of the entities the caller resolved (the model cannot invent one),
* the quoted evidence literally occurs at the stated span in the Signal text,
* lengths and charset are bounded and no URL smuggles a rendered link through.

A malformed whole output is a poison result for that Signal only (class ``extract``).
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from catchup_core.contracts import FailureClass
from catchup_core.domain_pack import DomainPack, Predicate
from catchup_core.rule_of_two import CallDeclaration

__all__ = [
    "Candidate", "ExtractionRequest", "ExtractionResult", "NormalizedSignal", "Rejection", "build_request",
    "parse_output",
]

MAX_CANDIDATES = 20
MAX_TEXT_VALUE = 280
MAX_QUOTE = 600
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TEMPLATE = (Path(__file__).parent / "prompts" / "extract_v1.md").read_text(encoding="utf-8")

EXTRACTOR_DECLARATION = CallDeclaration("extractor", untrusted=True, private=True, effect=False)


@dataclass(frozen=True)
class NormalizedSignal:
    signal_id: str
    connector: str
    text: str
    source_class: str = "school_email"


@dataclass(frozen=True)
class Candidate:
    predicate: str
    value: Any                       # typed: date / datetime / str
    subject_id: str
    signal_id: str
    span: tuple[int, int]
    quote: str
    confidence: float | None = None


@dataclass(frozen=True)
class Rejection:
    index: int
    reason: str


@dataclass(frozen=True)
class ExtractionResult:
    candidates: tuple[Candidate, ...]
    rejections: tuple[Rejection, ...] = ()
    failure: FailureClass | None = None
    failure_detail: str = ""

    @property
    def ok(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class ExtractionRequest:
    """What would be sent to a model.  ``tools`` is always empty (no effects)."""

    prompt: str
    signal_id: str
    tools: tuple[str, ...] = ()
    declaration: CallDeclaration = field(default=EXTRACTOR_DECLARATION)
    schema_version: str = "extract_v1"
    max_output_tokens: int = 1024


def build_request(pack: DomainPack, signal: NormalizedSignal, subjects: Sequence[str],
                  max_excerpt_chars: int = 8000) -> ExtractionRequest:
    """Render the extractor prompt with the *minimum* excerpt and the closed vocabularies."""
    predicates = []
    for p in pack.predicates.values():
        suffix = f" [{', '.join(p.values)}]" if p.values else ""
        fmt = {"date": " (YYYY-MM-DD)", "datetime": " (ISO-8601 with offset)"}.get(p.type, "")
        predicates.append(f"- {p.name}: {p.type}{fmt}{suffix}")
    excerpt = signal.text[:max_excerpt_chars]
    # The delimiter itself must not be forgeable from inside the message.
    excerpt = excerpt.replace("</untrusted>", "< /untrusted>").replace("<untrusted>", "< untrusted>")
    prompt = (
        _TEMPLATE.replace("{{subjects}}", ", ".join(sorted(subjects)))
        .replace("{{predicates}}", "\n".join(predicates))
        .replace("{{message}}", excerpt)
    )
    return ExtractionRequest(prompt=prompt, signal_id=signal.signal_id)


def _typed_value(pred: Predicate, raw: Any) -> Any:
    if not isinstance(raw, str):
        raise ValueError("value must be a string")
    if pred.type == "date":
        return date.fromisoformat(raw)
    if pred.type == "datetime":
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            raise ValueError("datetime needs a UTC offset")
        return parsed
    if pred.type == "enum":
        pred.validate_value(raw)
        return raw
    if len(raw) > MAX_TEXT_VALUE:
        raise ValueError("text value too long")
    if _CONTROL.search(raw) or _URL.search(raw):
        raise ValueError("text value contains control characters or a URL")
    return raw


def parse_output(output: str, pack: DomainPack, signal: NormalizedSignal,
                 subjects: Sequence[str]) -> ExtractionResult:
    """Validate model output.  Never raises; a bad whole output is a per-Signal failure."""
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return ExtractionResult((), failure=FailureClass.EXTRACT, failure_detail="output is not JSON")
    items = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ExtractionResult((), failure=FailureClass.EXTRACT, failure_detail="no 'candidates' list")
    if len(items) > MAX_CANDIDATES:
        return ExtractionResult((), failure=FailureClass.EXTRACT,
                                failure_detail=f"more than {MAX_CANDIDATES} candidates")

    allowed = set(subjects)
    accepted: list[Candidate] = []
    rejected: list[Rejection] = []
    for index, item in enumerate(items):
        reason = _check_item(item, pack, signal, allowed)
        if isinstance(reason, str):
            rejected.append(Rejection(index, reason))
        else:
            accepted.append(reason)
    return ExtractionResult(tuple(accepted), tuple(rejected))


def _check_evidence(item: dict[str, Any], text: str) -> tuple[int, int, str] | str:
    span, quote = item.get("span"), item.get("quote")
    if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(n, int) for n in span)
            or not isinstance(quote, str)):
        return "malformed evidence"
    start, end = span
    if not (0 <= start < end <= len(text)) or len(quote) > MAX_QUOTE:
        return "evidence span out of range"
    if text[start:end] != quote:
        return "quote does not match the message at the span"
    return start, end, quote


def _check_confidence(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        return "confidence out of range"
    return None


def _lookup(item: dict[str, Any], pack: DomainPack, allowed: set[str]) -> tuple[Predicate, str] | str:
    pred = pack.predicates.get(str(item.get("predicate")))
    if pred is None:
        return "unknown predicate"
    subject = item.get("subject_id")
    if not isinstance(subject, str) or subject not in allowed:
        return "unknown subject"
    return pred, subject


def _check_item(item: Any, pack: DomainPack, signal: NormalizedSignal, allowed: set[str]) -> Candidate | str:
    if not isinstance(item, dict):
        return "candidate is not an object"
    found = _lookup(item, pack, allowed)
    if isinstance(found, str):
        return found
    pred, subject = found
    evidence = _check_evidence(item, signal.text)
    if isinstance(evidence, str):
        return evidence
    try:
        value = _typed_value(pred, item.get("value"))
    except ValueError as exc:      # includes PackError for undeclared enum values
        return f"value rejected: {exc}"
    problem = _check_confidence(item.get("confidence"))
    if problem:
        return problem
    confidence = item.get("confidence")
    start, end, quote = evidence
    return Candidate(pred.name, value, subject, signal.signal_id, (start, end), quote,
                     float(confidence) if confidence is not None else None)
