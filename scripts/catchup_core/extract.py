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
_TEMPLATE = (Path(__file__).parent / "prompts" / "extract_v2.md").read_text(encoding="utf-8")

EXTRACTOR_DECLARATION = CallDeclaration("extractor", untrusted=True, private=True, effect=False)


@dataclass(frozen=True)
class NormalizedSignal:
    signal_id: str
    connector: str
    text: str
    source_class: str = "school_email"
    observed_at: str | None = None


@dataclass(frozen=True)
class Candidate:
    predicate: str
    value: Any                       # typed: date / datetime / str
    subject_id: str
    signal_id: str
    span: tuple[int, int]
    quote: str
    confidence: float | None = None
    obligation_key: str = ""
    obligation_ref: str = ""


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
    complete: bool = True
    other_obligations: tuple[dict[str, Any], ...] = ()

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
    schema_version: str = "extract_v2"
    max_output_tokens: int = 1024


def build_request(pack: DomainPack, signal: NormalizedSignal, subjects: Sequence[str],
                  max_excerpt_chars: int = 8000) -> ExtractionRequest:
    """Render the extractor prompt with the *minimum* excerpt and the closed vocabularies."""
    predicates = []
    for p in pack.predicates.values():
        suffix = f" [{', '.join(p.values)}]" if p.values else ""
        fmt = {"date": " (YYYY-MM-DD)", "datetime": " (ISO-8601 with offset)"}.get(p.type, "")
        hint = f" - {p.hint}" if p.hint else ""
        predicates.append(f"- {p.name}: {p.type}{fmt}{suffix}{hint}")
    excerpt = signal.text[:max_excerpt_chars]
    # The delimiter itself must not be forgeable from inside the message.
    excerpt = excerpt.replace("</untrusted>", "< /untrusted>").replace("<untrusted>", "< untrusted>")
    prompt = (
        _TEMPLATE.replace("{{subjects}}", ", ".join(sorted(subjects)))
        .replace("{{predicates}}", "\n".join(predicates))
        .replace("{{message}}", excerpt)
    )
    prompt = f"Source observation time: {signal.observed_at or 'unknown'}\n" + prompt
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
    allowed = set(subjects)
    accepted: list[Candidate] = []
    rejected: list[Rejection] = []
    complete = data.get("complete") is True
    if len(items) > MAX_CANDIDATES:
        # Check the first items rather than losing the whole output; the overflow is recorded and the
        # Signal is marked incomplete so it is never reported as fully interpreted.
        rejected.append(Rejection(MAX_CANDIDATES,
                                  f"candidate count over the limit of {MAX_CANDIDATES}: overflow not read"))
        complete = False
    for index, item in enumerate(items[:MAX_CANDIDATES]):
        try:
            reason = _check_item(item, pack, signal, allowed)
        except (TypeError, ValueError, AttributeError, OverflowError):
            reason = "candidate could not be checked"
        if isinstance(reason, str):
            rejected.append(Rejection(index, reason))
        else:
            accepted.append(reason)
    others = []
    raw_others = data.get("other_obligations", [])
    if not isinstance(raw_others, list) or len(raw_others) + len(items) > MAX_CANDIDATES:
        if raw_others:
            rejected.append(Rejection(-1, "other obligations over the limit or not a list"))
        return ExtractionResult(tuple(accepted), tuple(rejected), complete=False)
    allowed_domains = {"finance", "health", "home", "immigration", "travel", "calendar", "insurance",
                       "vehicle", "learning", "digital", "comms", "employment", "kids_school", "pets"}
    own_domains = set(pack.predicate_domains.values()) | {pack.domain}
    for item in raw_others:
        if (not isinstance(item, dict) or not isinstance(item.get("domain"), str)
                or item["domain"] not in allowed_domains or item["domain"] in own_domains
                or not isinstance(item.get("subject_id"), str) or item["subject_id"] not in allowed):
            rejected.append(Rejection(-1, "invalid non-pilot obligation"))
            continue
        checked = _check_evidence(item, signal.text)
        if isinstance(checked, str):
            rejected.append(Rejection(-1, checked))
            continue
        start, end, quote = checked
        others.append({"kind": "incumbent", "domain": item["domain"], "subject_id": item["subject_id"],
                       "span": [start, end], "quote": quote})
    return ExtractionResult(tuple(accepted), tuple(rejected), complete=complete,
                            other_obligations=tuple(others))


def _check_evidence(item: dict[str, Any], text: str) -> tuple[int, int, str] | str:
    span, quote = item.get("span"), item.get("quote")
    if (not isinstance(span, list) or len(span) != 2 or not all(type(n) is int for n in span)
            or not isinstance(quote, str)):
        return "malformed evidence"
    start, end = span
    if not (0 <= start < end <= len(text.encode('utf-8'))) or len(quote) > MAX_QUOTE:
        return "evidence span out of range"
    try:
        actual = evidence_slice(text, start, end)
    except (UnicodeError, ValueError):
        return "evidence span is not on UTF-8 boundaries"
    if actual != quote:
        return "quote does not match the message at the span"
    return start, end, quote


def evidence_slice(text: str, start: int, end: int, unit: str = 'utf8-byte') -> str:
    """One coordinate boundary for new UTF-8 spans and explicitly labelled legacy evidence."""
    if unit == 'unicode-codepoint':
        return text[start:end]
    if unit != 'utf8-byte':
        raise ValueError('unknown evidence coordinates')
    encoded = text.encode('utf-8')
    if not 0 <= start <= end <= len(encoded):
        raise ValueError('evidence span out of range')
    encoded[:start].decode('utf-8')  # also reject a start in the middle of a codepoint
    return encoded[start:end].decode('utf-8')


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
    start, end, quote = evidence
    stored_span = (start, end)
    # Sentence boundaries operate on characters; stored evidence coordinates remain UTF-8 bytes.
    cstart = len(signal.text.encode('utf-8')[:start].decode('utf-8'))
    cend = cstart + len(quote)
    # A selective span such as "approved" or "October 19" cannot hide the enclosing negation,
    # cancellation, or replacement.
    # A full stop inside a number such as "$50.50" does not end the sentence.
    stops = [m.start() for m in _SENTENCE_END.finditer(signal.text, 0, cstart)]
    left = max(signal.text.rfind("\n", 0, cstart), stops[-1] if stops else -1) + 1
    following = _SENTENCE_END.search(signal.text, cend)
    newline = signal.text.find("\n", cend)
    ends = [i for i in (following.start() if following else -1, newline) if i >= 0]
    right = min(ends) + 1 if ends else len(signal.text)
    sentence = signal.text[max(left, cstart - 250):min(right, cend + 250)]
    context = sentence if pred.type == "enum" else quote
    if not supports_value(pred, value, context, observed_at=signal.observed_at, message=signal.text):
        problem = "quoted evidence does not support the typed value"
    elif pred.type != "enum" and _sentence_contradicts(value, sentence, signal.observed_at):
        problem = "quoted evidence does not support the typed value: negated, cancelled, or replaced"
    obligation_ref = item.get("obligation_ref", "")
    if not isinstance(obligation_ref, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{0,64}", obligation_ref):
        problem = "invalid obligation reference"
    if problem:
        return problem
    confidence = item.get("confidence")
    return Candidate(pred.name, value, subject, signal.signal_id, stored_span, quote,
                     float(confidence) if confidence is not None else None, obligation_ref=obligation_ref)


_SENTENCE_END = re.compile(r"[.?!](?=\s|$)")
_NUMERIC_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\d/])")


def _month_first_proven(message: str) -> bool:
    """A digits-only date is read month-first only if this very message shows a date that cannot be day-first."""
    found = [(int(m.group(1)), int(m.group(2))) for m in _NUMERIC_DATE.finditer(message)]
    return any(b > 12 >= a for a, b in found) and not any(a > 12 for a, _ in found)


def _numeric_date_matches(value: date, quote: str, observed_at: str | None) -> bool:
    for m in _NUMERIC_DATE.finditer(quote):
        if (int(m.group(1)), int(m.group(2))) != (value.month, value.day):
            continue
        year = m.group(3)
        if year is not None:
            if (2000 + int(year) if len(year) == 2 else int(year)) == value.year:
                return True
        elif observed_at and observed_at[:4] == str(value.year):
            return True
    return False


# "the 25th of this month" / "on the 1st of next month": only with an explicit this/next-month anchor.
_RELATIVE_MONTH_DAY = re.compile(r"\b(?:on\s+)?the\s+(\d{1,2})(st|nd|rd|th)\s+(?:of\s+)?(this|next)\s+month\b")


def _ordinal_suffix(day: int) -> str:
    return "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def _relative_month_days(text: str, observed_at: str | None) -> list[tuple[date, tuple[int, int]]]:
    """Resolve relative month-day phrases against the Signal's observation date, never the wall clock.

    A day that does not exist in the resolved month (the 31st of a 30-day month) resolves to nothing.
    """
    try:
        observed = date.fromisoformat((observed_at or "")[:10])
    except ValueError:
        return []
    found = []
    for m in _RELATIVE_MONTH_DAY.finditer(text):
        day = int(m.group(1))
        if m.group(2) != _ordinal_suffix(day):
            continue
        year, month = observed.year, observed.month
        if m.group(3) == "next":
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        try:
            found.append((date(year, month, day), m.span()))
        except ValueError:
            continue
    return found


def _date_supported(value: date, normalized: str, observed_at: str | None, message: str | None) -> bool:
    if value.isoformat() in normalized:
        return True
    if any(day == value for day, _ in _relative_month_days(normalized, observed_at)):
        return True
    if message and _month_first_proven(message) and _numeric_date_matches(value, normalized, observed_at):
        return True
    month = value.strftime("%B").casefold()
    explicit_years = re.findall(r"\b(?:19|20)\d{2}\b", normalized)
    if explicit_years:
        year_matches = str(value.year) in explicit_years
    else:
        year_matches = bool(observed_at and observed_at[:4] == str(value.year))
    return year_matches and bool(re.search(
        rf"\b(?:{month}|{month[:3]}\.?)\s+0?{value.day}(?:st|nd|rd|th)?\b", normalized))


_HARD_STOP = re.compile(
    r"\b(?:cancel(?:l?ed|s)?|called off|no longer|rescinded|withdrawn|revoked|never|"
    r"not (?:be )?(?:required|needed|due)|is wrong|incorrect|disregard)\b")
_RESOLVED = re.compile(r"\b(?:submitted|received|turned in|completed|excused|resolved|paid|returned)\b")
_NEGATES_BEFORE = re.compile(
    r"\b(?:not|no|never|except|excluding|without|instead of|rather than|originally|previously|formerly|"
    r"was|were|from|until then|old|prior)\W*(?:\w+\W+){0,2}$")
_NEGATES_AFTER = re.compile(r"^\W*(?:(?:is|was|are|were|has been|have been|will be)\s+)?(?:not|no longer|never)\b")
_CHANGED = re.compile(r"\b(?:moved|postponed|rescheduled|changed|delayed|extended|pushed|updated|replaced)\b")
_NEW_BEFORE = re.compile(r"\b(?:to|until|now|new date|new deadline)\W*(?:\w+\W+){0,1}$")


def _date_mentions(value: date, text: str, observed_at: str | None) -> list[tuple[int, int]]:
    month = value.strftime("%B").casefold()
    forms = [re.escape(value.isoformat()),
             rf"\b(?:{month}|{month[:3]}\.?)\s+0?{value.day}(?:st|nd|rd|th)?\b(?:,?\s+{value.year})?",
             rf"(?<![\d/])0?{value.month}/0?{value.day}(?:/(?:{value.year}|{value.year % 100:02d}))?(?![\d/])"]
    return ([m.span() for f in forms for m in re.finditer(f, text)]
            + [span for day, span in _relative_month_days(text, observed_at) if day == value])


_MONTH_NAMES = (r"january|february|march|april|may|june|july|august|september|october|november|december|"
                r"jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")
_WEEKDAY_NAMES = (r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                  r"mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun")
_MONTH_INDEX = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                             "nov", "dec"), 1)}
_WEEKDAY_INDEX = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
# Offsets in minutes east of UTC; the generic US zone names allow standard or daylight time.
_ZONE_OFFSETS: dict[str, tuple[int, ...]] = {
    "utc": (0,), "gmt": (0,), "est": (-300,), "edt": (-240,), "cst": (-360,), "cdt": (-300,), "mst": (-420,),
    "mdt": (-360,), "pst": (-480,), "pdt": (-420,), "akst": (-540,), "akdt": (-480,), "hst": (-600,),
    "et": (-300, -240), "ct": (-360, -300), "mt": (-420, -360), "pt": (-480, -420)}
_WD = rf"(?:\b(?P<wd>{_WEEKDAY_NAMES})\b\.?,?\s+)?"
_YEAR = r"(?:,?\s+(?P<year>\d{4})\b)?"
_DATE_FORMS = (
    rf"{_WD}\b(?P<mon>{_MONTH_NAMES})\b\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b{_YEAR}",
    rf"{_WD}(?:\bthe\s+)?\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MONTH_NAMES})\b\.?{_YEAR}",
    rf"{_WD}(?<![\d/])(?P<a>\d{{1,2}})/(?P<b>\d{{1,2}})(?:/(?P<year>\d{{4}}|\d{{2}}))?(?![\d/])",
)
_TIME = (r"(?<![\d:])(?:(?P<h>\d{1,2})(?::(?P<mi>\d{2})(?::(?P<s>\d{2}))?)?\s*(?P<mer>[ap])\.?\s?m\b\.?"
         r"|(?P<h24>\d{2}):(?P<mi24>\d{2})(?::(?P<s24>\d{2}))?(?![\d:]|\s*[ap]\.?\s?m\b))"
         rf"(?:\s*\(?(?P<zone>(?:utc|gmt)\s*[+-]\d{{1,2}}(?::?\d{{2}})?|(?:{'|'.join(_ZONE_OFFSETS)})\b"
         r"|(?-i:[A-Z]{2,4}T)\b)\)?)?")
_DATETIME_FORMS = tuple(re.compile(p, re.IGNORECASE) for d in _DATE_FORMS for p in (
    rf"{d}(?:\s*,)?\s*(?:(?:at|@|-|\u2013)\s*)?{_TIME}", rf"{_TIME}\s*,?\s*(?:on\s+)?{d}"))


def _resolve_date(month: int, day: int, year: str | None, weekday: int | None,
                  observed_at: str | None) -> date | None:
    """Explicit year, or the nearest occurrence on/after the observation date that fits any stated weekday."""
    if year:
        years: Sequence[int] = [int(year) + (2000 if len(year) == 2 else 0)]
        seen = date.min
    else:
        if not observed_at:
            return None
        try:
            seen = date.fromisoformat(observed_at[:10])
        except ValueError:
            return None
        years = range(seen.year, seen.year + 29)    # weekday/date pairs repeat within 28 years
    for y in years:
        try:
            found = date(y, month, day)
        except ValueError:
            continue
        if found >= seen and (weekday is None or found.weekday() == weekday):
            return found
    return None


def _zone_agrees(zone: str, value: datetime) -> bool:
    name = "".join(zone.casefold().split())
    explicit = re.fullmatch(r"(?:utc|gmt)([+-])(\d{1,2})(?::?(\d{2}))?", name)
    if explicit:
        minutes = int(explicit.group(2)) * 60 + int(explicit.group(3) or 0)
        allowed: tuple[int, ...] = (-minutes if explicit.group(1) == "-" else minutes,)
    else:
        allowed = _ZONE_OFFSETS.get(name, ())     # an unrecognised zone cannot be checked
    offset = value.utcoffset()
    return offset is not None and int(offset.total_seconds() // 60) in allowed


def _datetime_match(m: re.Match[str], value: datetime, observed_at: str | None, message: str | None) -> bool:
    g = m.groupdict()
    if g["mer"]:
        if not 1 <= int(g["h"]) <= 12:
            return False
        clock = (int(g["h"]) % 12 + (12 if g["mer"].casefold() == "p" else 0), int(g["mi"] or 0), int(g["s"] or 0))
    else:
        clock = (int(g["h24"]), int(g["mi24"]), int(g["s24"] or 0))
    # Text without a zone is checked on wall-clock time only; a stated zone must agree with the offset.
    if clock != (value.hour, value.minute, value.second) or value.microsecond:
        return False
    if g["zone"] and not _zone_agrees(g["zone"], value):
        return False
    if g.get("mon"):
        month, day = _MONTH_INDEX[g["mon"][:3].casefold()], int(g["day"])
    else:
        # Same convention rule as date-only grounding: digits are read month-first only when proven.
        # Without a message (contradiction search) any month-first mention counts, as in _date_mentions.
        if message is not None and not _month_first_proven(message):
            return False
        month, day = int(g["a"]), int(g["b"])
    weekday = _WEEKDAY_INDEX[g["wd"][:3].casefold()] if g["wd"] else None
    return _resolve_date(month, day, g["year"], weekday, observed_at) == value.date()


def _datetime_mentions(value: datetime, text: str, observed_at: str | None,
                       message: str | None = None) -> list[tuple[int, int]]:
    """Spans where a natural-language date AND time resolve to the value; a date alone never counts."""
    return [m.span() for form in _DATETIME_FORMS for m in form.finditer(text)
            if _datetime_match(m, value, observed_at, message)]


def _mentions(value: Any, text: str, observed_at: str | None) -> list[tuple[int, int]]:
    if isinstance(value, datetime):
        return ([m.span() for m in re.finditer(re.escape(value.isoformat().casefold()), text)]
                + _datetime_mentions(value, text, observed_at))
    if isinstance(value, date):
        return _date_mentions(value, text, observed_at)
    needle = str(value).casefold().replace("_", " ")
    # Whole-token only: "$50" is not "$500" or "$50.50", "Ann" is not "Joanna", "Essay 2" is not "Essay 20".
    pattern = rf"(?<![\w$]){re.escape(needle)}(?![\w]|[.,]\d)"
    return [m.span() for m in re.finditer(pattern, text)]


def _sentence_contradicts(value: Any, sentence: str, observed_at: str | None) -> bool:
    """True when every mention of the value in its sentence is negated, cancelled, resolved, or replaced.

    Ambiguous cases are rejected here so they stay in review instead of becoming a Fact.
    """
    text = " ".join(sentence.casefold().replace("_", " ").split())
    spans = _mentions(value, text, observed_at)
    is_text = not isinstance(value, date)
    if not spans:
        return is_text          # a text value cited from the middle of a longer token is not that value
    if _HARD_STOP.search(text):
        return True
    if is_text and _RESOLVED.search(text):
        return True
    changed = bool(_CHANGED.search(text))

    def blocked(a: int, b: int) -> bool:
        before, after = text[:a], text[b:]
        if _NEGATES_BEFORE.search(before[-40:]) or _NEGATES_AFTER.search(after[:30]):
            return True
        return changed and not is_text and not _NEW_BEFORE.search(before[-25:])
    return all(blocked(a, b) for a, b in spans)


def supports_value(pred: Predicate, value: Any, quote: str, *, observed_at: str | None = None,
                   message: str | None = None) -> bool:
    """Conservative field grounding; ambiguous formats require explicit confirmation."""
    normalized = " ".join(quote.casefold().replace("_", " ").split())
    if isinstance(value, datetime):
        return value.isoformat().casefold() in normalized or bool(
            _datetime_mentions(value, " ".join(quote.split()), observed_at, message or quote))
    if isinstance(value, date):
        return _date_supported(value, normalized, observed_at, message)
    if pred.type == "enum":
        if "?" in normalized or re.search(
                r"\b(not|no|never|previously|formerly|may|might|could|if|unless|unconfirmed|revoked|"
                r"withdrawn|rescinded|expired|was|were|had|whether)\b", normalized):
            return False
        if any(re.search(r"\b" + re.escape(w.casefold().replace("_", " ")) + r"\b", normalized) for w in pred.unless):
            return False
        words = [str(value), *dict(pred.aliases).get(str(value), ())]
        return any(re.search(r"\b" + re.escape(w.casefold().replace("_", " ")) + r"\b", normalized) for w in words)
    return bool(_mentions(value, normalized, observed_at))
