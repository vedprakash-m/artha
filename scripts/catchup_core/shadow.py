"""Shadow comparison against the incumbent Briefing (re-artha §7.4, §12.6, RA-02).

An empty new ledger must not silently empty the user experience.  This module
extracts the incumbent's **P1/P2-class items** (🔴 Critical / 🟠 Urgent sections) from
its Briefing text and compares them with what the core surfaced.  Matching free text
to obligations is a *labeling* step owned by the reviewer: unlabeled incumbent items
are reported as ``needs_review`` and are never counted as matched.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from catchup_core.scorecard import Case

__all__ = ["Comparison", "as_case", "compare", "incumbent_items"]

_SECTION = re.compile(r"^[#━\s]*(🔴|🟠)\s*(?:\*\*)?\s*(critical|urgent)\b", re.IGNORECASE)
_NEXT_SECTION = re.compile(r"^[#━\s]*(🟡|🟢|🔵|⚪|ONE THING|[A-Z][A-Z &]{3,}$)")
_ITEM = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+(.*\S)")


@dataclass(frozen=True)
class Comparison:
    matched: tuple[str, ...]              # obligation keys both found
    core_only: tuple[str, ...]            # surfaced by the core, absent from the incumbent
    incumbent_only: tuple[str, ...]       # labeled incumbent items the core missed (regressions)
    needs_review: tuple[str, ...]         # incumbent items nobody has labeled yet

    @property
    def clean(self) -> bool:
        return not self.incumbent_only and not self.needs_review


def incumbent_items(briefing_text: str) -> list[str]:
    """Bullet items under the 🔴/🟠 sections of an incumbent Briefing."""
    items: list[str] = []
    inside = False
    for line in briefing_text.splitlines():
        if _SECTION.match(line):
            inside = True
            continue
        if inside and (_NEXT_SECTION.match(line) or line.startswith(("---", "━━━"))) and not _ITEM.match(line):
            inside = False
        if inside:
            m = _ITEM.match(line)
            if m:
                items.append(" ".join(m.group(1).split()))
    return items


def compare(core_keys: Iterable[str], incumbent_texts: Iterable[str], labels: Mapping[str, str]) -> Comparison:
    core = set(core_keys)
    labeled: set[str] = set()
    review: list[str] = []
    for text in incumbent_texts:
        key = labels.get(text)
        if key is None:
            review.append(text)
        else:
            labeled.add(key)
    return Comparison(
        matched=tuple(sorted(core & labeled)),
        core_only=tuple(sorted(core - labeled)),
        incumbent_only=tuple(sorted(labeled - core)),
        needs_review=tuple(sorted(review)),
    )


def as_case(case_id: str, comparison: Comparison, *, quiet_claim: bool, coverage_complete: bool,
            citations_resolve: bool) -> Case:
    """Scorecard input for one shadow Catch-up (the incumbent's labeled items are the expectation)."""
    expected = frozenset(comparison.matched) | frozenset(comparison.incumbent_only)
    return Case(case_id, expected=expected, surfaced=frozenset(comparison.matched) | frozenset(comparison.core_only),
                quiet_claim=quiet_claim, coverage_complete=coverage_complete, citations_resolve=citations_resolve,
                incumbent_surfaced=frozenset(comparison.matched) | frozenset(comparison.incumbent_only))
