"""Incumbent adapter interface and mixed-Signal routing (re-artha §6.3, §6.14).

The incumbent (instruction-driven) workflow keeps authority for non-pilot Domains.
The adapter below is the *only* way its output reaches the new core:

* inputs are permitted Signals only — pilot scopes are excluded before the call;
* outputs are typed conclusions with provenance ``legacy_state``, coverage and
  proposed record changes — it can neither archive nor deliver a Briefing;
* a proposed change to a pilot-owned record type is refused;
* a timeout or crash degrades the incumbent's coverage; it never fails the Catch-up.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from catchup_core.contracts import CoverageEntry, CoverageStatus, FailureClass

__all__ = [
    "GuardedIncumbent", "IncumbentAdapter", "IncumbentConclusion", "IncumbentOutput", "PilotWriteRefused",
    "ProposedChange", "RoutedItem", "route_mixed",
]


class PilotWriteRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class IncumbentConclusion:
    domain: str
    text: str
    state_file_version: str
    provenance: str = "legacy_state"
    kind: str = "record"            # "open_item" | "record"
    due: str = ""                   # ISO date for an open item, when known


@dataclass(frozen=True)
class ProposedChange:
    record_type: str
    record_id: str
    detail: str = ""


@dataclass(frozen=True)
class IncumbentOutput:
    conclusions: tuple[IncumbentConclusion, ...] = ()
    coverage: tuple[CoverageEntry, ...] = ()
    proposed_changes: tuple[ProposedChange, ...] = ()
    failures: tuple[FailureClass, ...] = ()


class IncumbentAdapter(Protocol):
    def run(self, signals: Sequence[str], *, budget_seconds: float) -> IncumbentOutput: ...


@dataclass(frozen=True)
class RoutedItem:
    signal_id: str
    domain: str
    obligation_ref: str            # candidate-level reference within the signal


def route_mixed(
    signal_id: str, obligations: Sequence[tuple[str, str]], pilot_domains: Sequence[str]
) -> tuple[list[RoutedItem], list[RoutedItem]]:
    """Fan one normalized Signal out by obligation: ``(pilot_items, incumbent_items)``.

    The Signal is normalized once; excluding its pilot portion never discards the
    rest, and each obligation appears in exactly one side.
    """
    pilot: list[RoutedItem] = []
    other: list[RoutedItem] = []
    pilots = set(pilot_domains)
    for ref, domain in obligations:
        item = RoutedItem(signal_id, domain, ref)
        (pilot if domain in pilots else other).append(item)
    return pilot, other


class GuardedIncumbent:
    def __init__(
        self,
        adapter: IncumbentAdapter | None,
        *,
        pilot_record_types: Sequence[str] = ("fact", "alert", "briefing"),
        clock: Callable[[], float] = time.monotonic,
    ):
        self._adapter = adapter
        self._pilot_types = frozenset(pilot_record_types)
        self._clock = clock

    def run(self, non_pilot_signals: Sequence[str], *, budget_seconds: float = 60.0) -> IncumbentOutput:
        if self._adapter is None:
            return IncumbentOutput()
        start = self._clock()
        try:
            out = self._adapter.run(list(non_pilot_signals), budget_seconds=budget_seconds)
        except Exception as exc:
            return IncumbentOutput(
                coverage=(CoverageEntry("incumbent", "non-pilot", CoverageStatus.UNAVAILABLE, required=False,
                                        detail=f"incumbent adapter failed: {type(exc).__name__}"),),
                failures=(FailureClass.UNKNOWN,))
        if self._clock() - start > budget_seconds:
            return IncumbentOutput(
                conclusions=out.conclusions,
                coverage=(*out.coverage, CoverageEntry("incumbent", "non-pilot", CoverageStatus.DEGRADED,
                                                       required=False, detail="exceeded its time budget")),
                proposed_changes=out.proposed_changes, failures=(*out.failures, FailureClass.TIMEOUT))
        forbidden = [c for c in out.proposed_changes if c.record_type in self._pilot_types]
        if forbidden:
            raise PilotWriteRefused(
                f"incumbent proposed a write to pilot-owned record type {forbidden[0].record_type!r}")
        bad = [c for c in out.conclusions if c.provenance != "legacy_state"]
        if bad:
            raise PilotWriteRefused("incumbent conclusions must carry legacy_state provenance")
        return out
