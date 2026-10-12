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

from catchup_core.triage import AlertDraft, CrossDomainFinding, TriageResult

__all__ = ["Briefing", "EvidenceRef", "EvidenceTable", "compose", "sanitize", "validate_briefing"]

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
    return d.strftime("%a %b %-d") if d else "date unknown"


def _alert_line(a: AlertDraft, cite: str) -> str:
    label = a.predicate.replace("_", " ")
    who = sanitize(a.subject_id, 40)
    sev = a.severity.value if a.severity else "unscored"
    if a.state == "disputed":
        core = f"{who}: {label} — sources disagree; confirm before acting"
    elif a.state == "no_deadline":
        core = f"{who}: {label} — no usable date; unknown"
    else:
        when = f"due {_fmt_date(a.deadline)}"
        if a.days_until is not None:
            when += f" ({a.days_until} day{'s' if abs(a.days_until) != 1 else ''})" if a.days_until >= 0 \
                else f" ({abs(a.days_until)} days overdue)"
        act = f"; act by {_fmt_date(a.latest_useful_action)}" if a.latest_useful_action else ""
        core = f"{who}: {label} {when}{act}"
    note = f" — {sanitize(a.uncertainty, 120)}" if a.uncertainty else ""
    return f"• [{sev}] {core}{note} {cite}"


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
) -> Briefing:
    """Render the standard Briefing.

    ``evidence_for`` maps an obligation key to ``(token, source, observed_at, scope)``
    tuples.  A consequential line without evidence is rendered with
    ``{{MISSING: …}}`` instead of an invented citation.
    """
    table = EvidenceTable()
    missing: list[str] = []
    lines: list[str] = [f"ARTHA · {now.strftime('%A, %b %-d')}", sanitize(coverage_line, 400), ""]

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
        lines += ["ONE THING", _alert_line(one, cite(one)), ""]
    if result.alerts[1:]:
        lines += ["ALERTS"] + [_alert_line(a, cite(a)) for a in result.alerts[1:]] + [""]
    upcoming = [h for h in result.horizon if h.obligation_key not in {a.obligation_key for a in result.alerts}]
    if upcoming:
        lines += ["COMING UP"] + [_alert_line(h, cite(h)) for h in upcoming] + [""]
    if result.cross_domain:
        lines += ["WORTH A LOOK"] + [_finding_line(f) for f in result.cross_domain] + [""]
    if legacy_lines:
        shown = list(legacy_lines)[:8]
        extra = f" (+{len(legacy_lines) - 8} more)" if len(legacy_lines) > 8 else ""
        lines += [f"FROM YOUR RECORDS (not re-verified){extra}"] + [f"• {sanitize(t, 200)}" for t in shown] + [""]
    if goal_lines:
        lines += ["GOALS"] + [f"• {sanitize(g, 200)}" for g in goal_lines] + [""]

    if quiet:
        lines += ["Nothing urgent in the sources checked."]
    elif not result.alerts and not result.horizon:
        gaps = "; ".join(sanitize(g, 100) for g in coverage_gaps) or "some inputs are incomplete or unknown"
        lines += [f"No alerts from verified sources, but this is not an all-clear: {gaps}."]
    if unresolved_signals:
        lines += [f"{unresolved_signals} fetched item(s) have not been interpreted yet."]

    if result.next_check_by:
        tail = "check now" if result.check_now else f"next check by {_fmt_date(result.next_check_by)}"
        lines += [f"Next check: {tail}."]

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
