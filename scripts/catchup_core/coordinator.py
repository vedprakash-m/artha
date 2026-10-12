"""Catch-up coordinator: the typed stage machine (re-artha §6.1, §6.5, §6.15).

``CREATED → PREFLIGHT → FETCH → NORMALIZE → EXTRACT → RECONCILE → TRIAGE → COMPOSE →
VALIDATE → COMMIT → PRESENTED → FINALIZED``.  Each stage commits a journal row; a
re-run with the same Catch-up ID **resumes at the first uncommitted stage** and
re-derives its inputs from the ledger, so no committed work is repeated.  Provider
calls, the model and the Briefing archive are injected, so replay uses saved Signals
and fault injection can fail every boundary.

Guarantees implemented here:

* a failed or rejecting Connector degrades exactly its scope — verified Alerts from
  other sources still publish, and no reassurance is written for the gap;
* a poison Signal (bad extraction) is isolated and keeps the Catch-up non-quiet;
* a forgotten source is never re-ingested;
* one Catch-up ID publishes at most one logical Briefing (unique, versioned);
* a shadow run compares and records but never publishes or archives.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from catchup_core import extract as ex
from catchup_core import ids
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.compose import compose, validate_briefing
from catchup_core.contracts import (
    CatchupRequest,
    CatchupResult,
    CoverageEntry,
    CoverageStatus,
    ExecutionMode,
    FailureClass,
    ProcessingState,
    RunStatus,
    Stage,
    StageCheckpoint,
)
from catchup_core.domain_pack import DomainPack
from catchup_core.incumbent import GuardedIncumbent, IncumbentOutput
from catchup_core.ledger import Ledger, LedgerError, SignalInput, Suppressed
from catchup_core.reconcile import reconcile
from catchup_core.triage import CalendarEvent, TriageResult, triage

__all__ = ["ConnectorFetch", "ConnectorRecord", "Deps", "StageFault", "run_catchup"]

MAX_SIGNAL_CHARS = 200_000


@dataclass(frozen=True)
class ConnectorRecord:
    upstream_id: str
    text: str
    provider_version: str | None = None
    occurred_at: str | None = None


@dataclass(frozen=True)
class ConnectorFetch:
    connector: str
    account_scope: str
    records: tuple[ConnectorRecord, ...] = ()
    status: CoverageStatus = CoverageStatus.COMPLETE
    error: FailureClass | None = None
    blind_spots: tuple[str, ...] = ()
    required: bool = True


class StageFault(RuntimeError):
    """Raised by fault-injection hooks to simulate a crash at a stage boundary."""


@dataclass
class Deps:
    ledger: Ledger
    pack: DomainPack
    blobs: EncryptedBlobStore
    id_key: ids.HmacKey
    fetchers: Mapping[str, Callable[[], ConnectorFetch]]
    extractor: Callable[[str], str]
    subjects: Sequence[str]
    viewer: str
    now: Callable[[], datetime]
    source_class: Mapping[str, str] = field(default_factory=dict)
    incumbent: GuardedIncumbent = field(default_factory=lambda: GuardedIncumbent(None))
    calendar: Callable[[], Sequence[CalendarEvent]] = lambda: ()
    obligation_states: Callable[[], Mapping[str, str]] = lambda: {}
    publisher: Callable[[str, str, int], None] | None = None
    fault: Callable[[Stage], None] | None = None
    coverage_line: Callable[[], str] = lambda: "Coverage: not summarized."


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _Run:
    """One Catch-up execution; each ``_stage_*`` method is one journaled stage."""

    def __init__(self, request: CatchupRequest, deps: Deps):
        self.request, self.deps, self.led = request, deps, deps.ledger
        self.now = deps.now()
        self.failures: list[FailureClass] = []
        self._incumbent: IncumbentOutput | None = None
        self.done = set(self.led.committed_stages(request.catchup_id))
        self.versions = {"policy": request.policy_version, "pack": deps.pack.version,
                         "schema": str(request.schema_version)}

    # ── journal ──────────────────────────────────────────────────────────────
    def stage(self, name: Stage, body: Callable[[], tuple[str, ...]]) -> None:
        if name.value in self.done:
            return
        if self.deps.fault is not None:
            self.deps.fault(name)
        artifacts = body()
        self.led.record_stage(StageCheckpoint(self.request.catchup_id, name, "committed", self.versions, artifacts))
        self.done.add(name.value)

    # ── FETCH ────────────────────────────────────────────────────────────────
    def _fetch_one(self, name: str, fetcher: Callable[[], ConnectorFetch]) -> list[str]:
        d = self.deps
        try:
            got = fetcher()
        except Exception:  # degrade only this connector's scope
            self.failures.append(FailureClass.PROVIDER)
            self.led.set_coverage(name, "unknown", CoverageStatus.UNAVAILABLE.value)
            return []
        if got.error is not None:
            self.failures.append(got.error)
        self.led.set_coverage(got.connector, got.account_scope, got.status.value, got.blind_spots,
                              completed_at=_iso(self.now) if got.status is CoverageStatus.COMPLETE else None)
        created: list[str] = []
        for rec in got.records:
            chmac = ids.content_hmac(d.id_key, rec.text)
            token = ids.version_token(rec.provider_version, chmac)
            ref = d.blobs.put(f"{got.connector}|{got.account_scope}|{rec.upstream_id}|{token}", rec.text)
            try:
                sid, is_new = self.led.upsert_signal(SignalInput(
                    got.connector, got.account_scope, rec.upstream_id, token, _iso(self.now), chmac, ref,
                    occurred_at=rec.occurred_at))
            except Suppressed:
                d.blobs.delete(ref)                  # forgotten: leave nothing behind
                continue
            if is_new:
                created.append(sid)
        return created

    def _stage_fetch(self) -> tuple[str, ...]:
        wanted = set(self.request.connector_scopes)
        out: list[str] = []
        for name, fetcher in self.deps.fetchers.items():
            if not wanted or name in wanted:
                out.extend(self._fetch_one(name, fetcher))
        return tuple(out)

    # ── NORMALIZE ────────────────────────────────────────────────────────────
    def _stage_normalize(self) -> tuple[str, ...]:
        out: list[str] = []
        for row in self.led.signals_in_state(ProcessingState.FETCHED):
            sid = row["signal_id"]
            try:
                text = self.deps.blobs.get(row["evidence_blob_ref"])
            except Exception:
                self.led.set_processing_state(sid, ProcessingState.EXCLUDED, "evidence unreadable")
                self.failures.append(FailureClass.DECODE)
                continue
            if not text.strip():
                self.led.set_processing_state(sid, ProcessingState.EXCLUDED, "empty message")
            elif len(text) > MAX_SIGNAL_CHARS:
                self.led.set_processing_state(sid, ProcessingState.EXCLUDED, "oversized: not interpreted")
                self.led.record_failure(sid, "normalize", FailureClass.DECODE.value)
                self.failures.append(FailureClass.DECODE)
            else:
                self.led.set_processing_state(sid, ProcessingState.NORMALIZED)
                out.append(sid)
        return tuple(out)

    # ── EXTRACT ──────────────────────────────────────────────────────────────
    def _extract_one(self, row: sqlite3.Row) -> bool:
        d, sid = self.deps, row["signal_id"]
        text = d.blobs.get(row["evidence_blob_ref"])
        klass = d.source_class.get(row["connector"], row["connector"])
        signal = ex.NormalizedSignal(sid, row["connector"], text, klass)
        try:
            output = d.extractor(ex.build_request(d.pack, signal, d.subjects).prompt)
        except Exception:                            # model failure: isolate, stay non-quiet
            output = ""
        parsed = ex.parse_output(output, d.pack, signal, d.subjects)
        if not parsed.ok:
            self.led.record_failure(sid, "extract", FailureClass.EXTRACT.value)
            self.failures.append(FailureClass.EXTRACT)
            return False
        if parsed.rejections:
            self.failures.append(FailureClass.SCHEMA_REJECTED)
        self.led.put_candidates(sid, [{
            "predicate": c.predicate,
            "value": c.value.isoformat() if hasattr(c.value, "isoformat") else c.value,
            "subject_id": c.subject_id, "span": list(c.span), "quote": c.quote} for c in parsed.candidates])
        self.led.clear_failure(sid, "extract")
        self.led.set_processing_state(sid, ProcessingState.EXTRACTED)
        return True

    def _stage_extract(self) -> tuple[str, ...]:
        return tuple(r["signal_id"] for r in self.led.signals_in_state(ProcessingState.NORMALIZED)
                     if self._extract_one(r))

    # ── RECONCILE ────────────────────────────────────────────────────────────
    def _source_class(self, signal_id: str) -> str:
        row = self.led.signal_row(signal_id)
        connector = row["connector"] if row else ""
        return self.deps.source_class.get(connector, connector)

    def _upstream(self, signal_id: str) -> tuple[str, str, str]:
        row = self.led.signal_row(signal_id)
        return (row["connector"], row["account_scope"], row["upstream_id"]) if row else ("", "", signal_id)

    def _stage_reconcile(self) -> tuple[str, ...]:
        cands: list[ex.Candidate] = []
        rows = self.led.signals_in_state(ProcessingState.EXTRACTED)
        for row in rows:
            for payload in self.led.candidates_for(row["signal_id"]):
                kind = self.deps.pack.predicates[payload["predicate"]].type
                cands.append(ex.Candidate(
                    payload["predicate"], _typed(kind, payload["value"]), payload["subject_id"],
                    row["signal_id"], (payload["span"][0], payload["span"][1]), payload["quote"]))
        result = reconcile(self.led, self.deps.pack, cands, observed_at=_iso(self.now),
                           source_class_of=self._source_class, upstream_key_of=self._upstream,
                           rule_version=self.request.policy_version)
        for row in rows:
            self.led.set_processing_state(row["signal_id"], ProcessingState.RECONCILED)
        return tuple(r["signal_id"] for r in rows) + tuple(c.fact_id for c in result.changes)

    # ── TRIAGE / COMPOSE / VALIDATE ──────────────────────────────────────────
    def _connector_of(self, signal_id: str) -> str:
        row = self.led.signal_row(signal_id)
        return str(row["connector"]) if row else ""

    def _triage(self) -> TriageResult:
        freshness = {r["connector"]: datetime.fromisoformat(r["completed_at"].replace("Z", "+00:00"))
                     for r in self.led.coverage_rows() if r["completed_at"]}
        return triage(
            self.led, self.deps.pack, self.now, viewer=self.deps.viewer, freshness=freshness,
            calendar=self.deps.calendar(), obligation_states=self.deps.obligation_states(),
            connector_of=self._connector_of,
            source_class_of=lambda sid: self.deps.source_class.get(self._connector_of(sid), self._connector_of(sid)))

    def _evidence_for(self, result: TriageResult) -> dict[str, list[tuple[str, str, str, str]]]:
        out: dict[str, list[tuple[str, str, str, str]]] = {}
        for a in result.alerts + result.horizon:
            try:
                fact = self.led.get_fact(a.fact_id, self.deps.viewer)
            except (LedgerError, KeyError):
                continue
            refs = []
            for sid, _s, _e in fact.evidence:
                row = self.led.signal_row(sid)
                if row is not None:
                    refs.append((f"evidence:{row['evidence_blob_ref']}", str(row["connector"]),
                                 str(row["observed_at"]), str(row["account_scope"])))
            out[a.obligation_key] = refs
        return out

    def _coverage(self) -> list[CoverageEntry]:
        d = self.deps
        entries = [
            CoverageEntry(r["connector"], r["account_scope"], CoverageStatus(r["status"]),
                          required=next((s.required for s in d.pack.sources if s.connector == r["connector"]), False),
                          last_verified=r["completed_at"])
            for r in self.led.coverage_rows()
        ]
        present = {e.connector for e in entries}
        for source in d.pack.sources:
            if source.required and source.connector not in present:
                entries.append(CoverageEntry(source.connector, "unconnected", CoverageStatus.UNAVAILABLE, True,
                                             detail="no fetch result recorded"))
        entries.extend(self._incumbent_output().coverage)
        return entries

    def _incumbent_output(self) -> IncumbentOutput:
        """The guarded incumbent runs once per Catch-up (never writes, never publishes)."""
        if self._incumbent is None:
            self._incumbent = self.deps.incumbent.run([])
            self.failures.extend(self._incumbent.failures)
        return self._incumbent

    # ── driver ───────────────────────────────────────────────────────────────
    def run(self) -> CatchupResult:
        req, led = self.request, self.led
        self.stage(Stage.CREATED, lambda: ())
        self.stage(Stage.PREFLIGHT, lambda: ())
        self.stage(Stage.FETCH, self._stage_fetch)
        self.stage(Stage.NORMALIZE, self._stage_normalize)
        self.stage(Stage.EXTRACT, self._stage_extract)
        self.stage(Stage.RECONCILE, self._stage_reconcile)

        triaged = self._triage()
        self.stage(Stage.TRIAGE, lambda: tuple(a.obligation_key for a in triaged.alerts))
        coverage = self._coverage()
        gaps = [f"{c.connector} {c.status.value}" + (f" ({c.detail})" if c.detail else "")
                for c in coverage if c.required and not c.status.quiet_claim_allowed]
        unresolved = len(led.unresolved_signals())
        has_gaps = bool(gaps) or unresolved > 0
        line = self.deps.coverage_line()

        legacy = [c.text for c in self._incumbent_output().conclusions]
        briefing = compose(self.now, triaged, evidence_for=self._evidence_for(triaged), coverage_line=line,
                           coverage_gaps=gaps, unresolved_signals=unresolved, legacy_lines=legacy)
        self.stage(Stage.COMPOSE, lambda: ())
        problems = validate_briefing(briefing.text, briefing.evidence, has_gaps=has_gaps)
        if problems:                                 # one safe retry: citation-dependent claims become MISSING
            briefing = compose(self.now, triaged, evidence_for={}, coverage_line=line, coverage_gaps=gaps,
                               unresolved_signals=unresolved, legacy_lines=legacy)
            problems = validate_briefing(briefing.text, briefing.evidence, has_gaps=has_gaps)
        if problems:
            self.failures.append(FailureClass.POLICY)
            return CatchupResult(
                req.catchup_id, req.mode, RunStatus.BLOCKED, coverage=tuple(coverage),
                reason_unpublished="Briefing failed validation: " + "; ".join(problems[:3]),
                failures=tuple(dict.fromkeys(self.failures)))
        self.stage(Stage.VALIDATE, lambda: ())

        briefing_id, version = self._commit(briefing.text)
        self.stage(Stage.PRESENTED, lambda: ())
        self.stage(Stage.FINALIZED, lambda: ())

        failures = tuple(dict.fromkeys(self.failures))
        status = CatchupResult.derive_status(tuple(coverage), failures)
        if unresolved and status is RunStatus.COMPLETE:
            status = RunStatus.DEGRADED
        return CatchupResult(
            req.catchup_id, req.mode, status, coverage=tuple(coverage), briefing_id=briefing_id,
            briefing_version=version, reason_unpublished=None if briefing_id else "shadow run",
            failures=failures, artifact_ids=tuple(a.fact_id for a in triaged.alerts))

    def _commit(self, text: str) -> tuple[str | None, int | None]:
        req = self.request
        if req.mode is not ExecutionMode.AUTHORITATIVE:
            self.stage(Stage.COMMIT, lambda: ())
            return None, None
        published: list[int] = []

        def do_commit() -> tuple[str, ...]:
            version, created = self.led.publish_briefing(req.catchup_id, text)
            published.append(version)
            if created and self.deps.publisher is not None:
                self.deps.publisher(req.catchup_id, text, version)
            return (f"brf_{req.catchup_id}",)

        self.stage(Stage.COMMIT, do_commit)
        if not published:                            # resumed after COMMIT: read the version back
            published.append(self.led.publish_briefing(req.catchup_id, text)[0])
        return f"brf_{req.catchup_id}", published[0]


def run_catchup(request: CatchupRequest, deps: Deps) -> CatchupResult:
    """Run (or resume) one Catch-up and return its typed result."""
    return _Run(request, deps).run()


def _typed(kind: str, raw: object) -> object:
    from datetime import date  # noqa: PLC0415
    if kind == "date":
        return date.fromisoformat(str(raw))
    if kind == "datetime":
        return datetime.fromisoformat(str(raw))
    return raw
