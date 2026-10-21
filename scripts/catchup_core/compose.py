"""Briefing composition and citation validation (re-artha §6.16, §7.3, RA-04).

The composer renders **typed** triage output through a template.  It never sees raw
untrusted text; every dynamic string is sanitized (no URLs, images, HTML) and every
consequential line carries a table-index citation that code resolves to the
evidence token, source and observation time.  :func:`validate_briefing` is the gate:
unresolved citations, uncited consequential claims, quiet-day language over a
coverage gap and rendered links all fail it.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from catchup_core.contracts import BriefingFormat
from catchup_core.triage import AlertDraft, CrossDomainFinding, TriageResult

__all__ = ["Briefing", "EvidenceRef", "EvidenceTable", "OpenItemLine", "compose", "sanitize", "validate_briefing"]

_URL = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+")
_HTML = re.compile(r"<[^>]{1,200}>")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CITE = re.compile(r"\[(\d{1,3})\]")
_MISSING = re.compile(r"\{\{MISSING: [^}]{1,120}\}\}")
_QUIET_PHRASES = ("all clear", "nothing urgent", "nothing to do", "nothing needs your attention")


def sanitize(text: str, limit: int = 160) -> str:
    """Strip anything that could render as a link, image or markup; bound length."""
    out = _IMAGE.sub("", text)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub("[link removed]", out)
    out = _HTML.sub("", out)
    out = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 1] + "…"


@dataclass(frozen=True)
class EvidenceRef:
    index: int
    token: str
    source: str
    observed_at: str
    scope: str


class EvidenceTable:
    """Per-request table: the model (or template) cites an index, code resolves it."""

    def __init__(self) -> None:
        self._by_token: dict[str, EvidenceRef] = {}

    def add(self, token: str, source: str, observed_at: str, scope: str = "") -> int:
        if token not in self._by_token:
            self._by_token[token] = EvidenceRef(len(self._by_token) + 1, token, source, observed_at, scope)
        return self._by_token[token].index

    def resolve(self, index: int) -> EvidenceRef | None:
        for ref in self._by_token.values():
            if ref.index == index:
                return ref
        return None

    def __len__(self) -> int:
        return len(self._by_token)

    def refs(self) -> list[EvidenceRef]:
        return sorted(self._by_token.values(), key=lambda r: r.index)


@dataclass(frozen=True)
class Briefing:
    text: str
    one_thing: AlertDraft | None
    quiet: bool
    evidence: tuple[EvidenceRef, ...]
    missing: tuple[str, ...] = field(default_factory=tuple)


def _fmt_date(d: date | None) -> str:
    return f"{d:%a %b} {d.day}" if d else "date unknown"


def _alert_line(a: AlertDraft, cite: str, labels: Mapping[str, str]) -> str:
    label = a.predicate.replace("_", " ")
    who = sanitize(labels.get(a.subject_id, a.subject_id), 40)
    sev = a.severity.value if a.severity else "unscored"
    if a.state == "disputed":
        core = f"{who}: {label} — sources disagree; confirm before acting"
    elif a.state == "no_deadline":
        core = f"{who}: {label} — no usable date; unknown"
    elif a.deadline is None:
        core = f"{who}: {label}"
    else:
        when = f"due {_fmt_date(a.deadline)}"
        if a.days_until is not None:
            when += f" ({a.days_until} day{'s' if abs(a.days_until) != 1 else ''})" if a.days_until >= 0 \
                else f" ({abs(a.days_until)} days overdue)"
        act = f"; act by {_fmt_date(a.latest_useful_action)}" if a.latest_useful_action else ""
        core = f"{who}: {label} {when}{act}"
    note = f" — {sanitize(a.uncertainty, 120)}" if a.uncertainty else ""
    return f"• [{sev}] {core}{note} {cite}"


@dataclass(frozen=True)
class OpenItemLine:
    text: str
    due: date | None


_DIGEST_WORDS = 300
_OPEN_ITEM_LIMIT = {BriefingFormat.FLASH: 0, BriefingFormat.DIGEST: 0, BriefingFormat.STANDARD: 5,
                    BriefingFormat.DEEP: 1000}


def _open_item_section(items: Sequence[OpenItemLine], today: date, fmt: BriefingFormat) -> list[str]:
    """Overdue first, then soonest; undated last.  Counts always show what was left out."""
    if not items:
        return []
    overdue = sum(1 for i in items if i.due is not None and i.due < today)
    ordered = sorted(items, key=lambda i: (i.due is None, i.due or date.max, i.text))
    limit = _OPEN_ITEM_LIMIT[fmt]
    head = f"OPEN ITEMS ({len(items)}" + (f", {overdue} overdue" if overdue else "") + ")"
    if fmt in (BriefingFormat.FLASH, BriefingFormat.DIGEST):
        return [head + " — `items` lists them"] if overdue else []
    lines = [head] + [f"• {sanitize(i.text, 200)}" for i in ordered[:limit]]
    if len(ordered) > limit:
        lines.append(f"  …and {len(ordered) - limit} more — say `show everything`")
    return [*lines, ""]


def _trim_words(lines: list[str], limit: int) -> list[str]:
    """Digest cap: drop the lowest-value trailing lines, never the coverage or next-check lines."""
    words = 0
    out: list[str] = []
    for line in lines:
        words += len(line.split())
        if words > limit and not line.startswith(("Next check", "Coverage", "No alerts", "Nothing urgent")):
            continue
        out.append(line)
    if len(out) < len(lines):
        out.insert(max(0, len(out) - 1), "(digest trimmed — say `brief` for the full Briefing)")
    return out


def _records_section(legacy_lines: Sequence[str], fmt: BriefingFormat) -> list[str]:
    """State Files that are only 'from your records': listed in deep, counted in standard, absent otherwise."""
    if not legacy_lines:
        return []
    if fmt is BriefingFormat.DEEP:
        return [f"FROM YOUR RECORDS (not re-verified; {len(legacy_lines)})"] + [
            f"• {sanitize(t, 200)}" for t in legacy_lines] + [""]
    if fmt is BriefingFormat.STANDARD:
        return [f"{len(legacy_lines)} other State File(s) are from your records and not re-verified "
                "— say `show everything` to list them.", ""]
    return []


def _verdict_lines(result: TriageResult, quiet: bool, coverage_gaps: Sequence[str], unresolved: int) -> list[str]:
    """The quiet claim is only ever made when coverage and closure allow it (RA-01, RA-16)."""
    lines: list[str] = []
    if quiet:
        lines.append("Nothing urgent in the sources checked.")
    elif not result.alerts and not result.horizon:
        gaps = "; ".join(sanitize(g, 100) for g in coverage_gaps) or "some inputs are incomplete or unknown"
        lines.append(f"No alerts from verified sources, but this is not an all-clear: {gaps}.")
    if unresolved:
        lines.append(f"{unresolved} fetched item(s) have not been interpreted yet.")
    return lines


def compose(
    now: datetime,
    result: TriageResult,
    *,
    evidence_for: Mapping[str, Sequence[tuple[str, str, str, str]]],
    coverage_line: str,
    coverage_gaps: Sequence[str] = (),
    unresolved_signals: int = 0,
    goal_lines: Sequence[str] = (),
    legacy_lines: Sequence[str] = (),
    subject_labels: Mapping[str, str] | None = None,
    fmt: BriefingFormat = BriefingFormat.STANDARD,
    open_items: Sequence[OpenItemLine] = (),
    calendar_line: str = "",
) -> Briefing:
    """Render the standard Briefing.

    ``evidence_for`` maps an obligation key to ``(token, source, observed_at, scope)``
    tuples.  A consequential line without evidence is rendered with
    ``{{MISSING: …}}`` instead of an invented citation.
    """
    table = EvidenceTable()
    labels = subject_labels or {}
    missing: list[str] = []
    lines: list[str] = [f"ARTHA · {now:%A, %b} {now.day}", sanitize(coverage_line, 400), ""]

    def cite(a: AlertDraft) -> str:
        refs = evidence_for.get(a.obligation_key, ())
        if not refs:
            missing.append(a.obligation_key)
            return "{{MISSING: no evidence recorded}}"
        return " ".join(f"[{table.add(*r)}]" for r in refs)

    one = result.alerts[0] if result.alerts else None
    has_gaps = bool(coverage_gaps) or unresolved_signals > 0 or bool(result.uncertain)
    quiet = not result.alerts and not result.horizon and not has_gaps

    if one is not None:
        lines += ["ONE THING", _alert_line(one, cite(one), labels), ""]
    brief = fmt in (BriefingFormat.FLASH, BriefingFormat.DIGEST)
    extra = result.alerts[1:] if fmt is not BriefingFormat.FLASH else ()
    if fmt is BriefingFormat.DIGEST:
        extra = extra[:3]
    if extra:
        lines += ["ALERTS"] + [_alert_line(a, cite(a), labels) for a in extra] + [""]
    upcoming = [h for h in result.horizon if h.obligation_key not in {a.obligation_key for a in result.alerts}]
    if upcoming and not brief:
        lines += ["COMING UP"] + [_alert_line(h, cite(h), labels) for h in upcoming] + [""]
    if calendar_line and not brief:
        lines += [sanitize(calendar_line, 200), ""]
    lines += _open_item_section(open_items, now.date(), fmt)
    if result.cross_domain and not brief:
        lines += ["WORTH A LOOK"] + [
            _finding_line(f) + " " + " ".join(f"[{table.add(*r)}]"
                for r in evidence_for.get(f.obligation_key, ()))
            for f in result.cross_domain] + [""]
    lines += _records_section(legacy_lines, fmt)
    if goal_lines and not brief:
        lines += ["GOALS"] + [f"• {sanitize(g, 200)}" for g in goal_lines] + [""]
    lines += _verdict_lines(result, quiet, coverage_gaps, unresolved_signals)

    if result.next_check_by:
        tail = "check now" if result.check_now else f"next check by {_fmt_date(result.next_check_by)}"
        lines += [f"Next check: {tail}."]
    else:
        lines += ["Next check: within 48 hours; sooner if a source changes."]

    if fmt is BriefingFormat.DIGEST:
        lines = _trim_words(lines, _DIGEST_WORDS)
    refs = table.refs()
    if refs:
        lines += ["", "Evidence"] + [
            f"[{r.index}] {sanitize(r.source, 60)}, observed {sanitize(r.observed_at, 30)}"
            f"{', ' + sanitize(r.scope, 40) if r.scope else ''}" for r in refs]
    return Briefing("\n".join(lines).rstrip() + "\n", one, quiet, tuple(refs), tuple(missing))


def _finding_line(f: CrossDomainFinding) -> str:
    suggestion = f" Consider {_fmt_date(f.suggested_date)}." if f.suggested_date else ""
    return f"• {sanitize(f.detail, 200)}.{suggestion}"


def validate_briefing(text: str, table: Sequence[EvidenceRef], *, has_gaps: bool) -> list[str]:
    """Return every violation (empty list means the Briefing may be published)."""
    problems: list[str] = []
    known = {r.index for r in table}
    for match in _CITE.finditer(text):
        if int(match.group(1)) not in known:
            problems.append(f"citation [{match.group(1)}] does not resolve to evidence")
    if _URL.search(text) or _IMAGE.search(text) or _HTML.search(text):
        problems.append("output contains a link, image or markup")
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("• [P") and not (_CITE.search(line) or _MISSING.search(line)):
            problems.append(f"line {number}: consequential claim without a citation")
    lowered = text.lower()
    if has_gaps and any(p in lowered for p in _QUIET_PHRASES) and "not an all-clear" not in lowered:
        problems.append("quiet-day language used while coverage has gaps")
    return problems
