"""Release scorecard (re-artha §11.2, §12.6): decide whether a shadow run may gate.

Pure arithmetic over labeled cases.  The hard conditions are never averaged away:
one false quiet claim, one citation that does not resolve, or one P1/P2 the
incumbent found and the core missed fails the release.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Case", "Scorecard", "score"]


@dataclass(frozen=True)
class Case:
    case_id: str
    expected: frozenset[str]               # obligation keys that deserve an Alert (P1/P2 class)
    surfaced: frozenset[str]               # keys the core surfaced
    quiet_claim: bool = False              # the Briefing said "nothing urgent" for this case
    coverage_complete: bool = True
    citations_resolve: bool = True
    incumbent_surfaced: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Scorecard:
    cases: int
    recall: float
    precision: float
    false_quiet: int
    unresolved_citations: int
    regressions_vs_incumbent: int

    @property
    def passes(self) -> bool:
        return (self.cases > 0 and self.false_quiet == 0 and self.unresolved_citations == 0
                and self.regressions_vs_incumbent == 0 and self.recall >= 1.0)

    def failures(self) -> list[str]:
        out: list[str] = []
        if not self.cases:
            out.append("no evaluated cases")
        if self.false_quiet:
            out.append(f"{self.false_quiet} false quiet claim(s)")
        if self.unresolved_citations:
            out.append(f"{self.unresolved_citations} case(s) with unresolved citations")
        if self.regressions_vs_incumbent:
            out.append(f"{self.regressions_vs_incumbent} P1/P2 found by the incumbent and missed")
        if self.recall < 1.0:
            out.append(f"recall {self.recall:.2f} < 1.00")
        return out


def score(cases: Sequence[Case]) -> Scorecard:
    expected = sum(len(c.expected) for c in cases)
    found = sum(len(c.expected & c.surfaced) for c in cases)
    surfaced = sum(len(c.surfaced) for c in cases)
    correct = sum(len(c.surfaced & c.expected) for c in cases)
    false_quiet = sum(1 for c in cases if c.quiet_claim and (c.expected - c.surfaced or not c.coverage_complete))
    regressions = sum(len((c.incumbent_surfaced & c.expected) - c.surfaced) for c in cases)
    return Scorecard(
        cases=len(cases),
        recall=(found / expected) if expected else 1.0,
        precision=(correct / surfaced) if surfaced else 1.0,
        false_quiet=false_quiet,
        unresolved_citations=sum(1 for c in cases if not c.citations_resolve),
        regressions_vs_incumbent=regressions,
    )
