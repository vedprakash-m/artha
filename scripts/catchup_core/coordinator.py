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

import hashlib
import json
import sqlite3
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from catchup_core import extract as ex
from catchup_core import ids
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.compose import Briefing, OpenItemLine, compose, validate_briefing
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
from catchup_core.crypto import DecryptionError, KeyUnavailableError
from catchup_core.domain_pack import DomainPack
from catchup_core.exchange import AwaitingExtraction, ExtractionPending, PendingRequest
from catchup_core.incumbent import GuardedIncumbent, IncumbentOutput
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied, SignalInput, Suppressed
from catchup_core.reconcile import reconcile
from catchup_core.session_lock import SessionBusy, session_lock
from catchup_core.triage import CalendarEvent, TriageResult, triage

__all__ = ["ConnectorFetch", "ConnectorRecord", "Deps", "StageFault", "run_catchup"]

MAX_SIGNAL_CHARS = 200_000


@dataclass(frozen=True)
class ConnectorRecord:
    upstream_id: str
    text: str
    provider_version: str | None = None
    occurred_at: str | None = None
    incomplete: bool = False
    calendar: CalendarEvent | None = None


@dataclass(frozen=True)
class ConnectorFetch:
    connector: str
    account_scope: str
    records: tuple[ConnectorRecord, ...] = ()
    status: CoverageStatus = CoverageStatus.COMPLETE
    error: FailureClass | None = None
    blind_spots: tuple[str, ...] = ()
    required: bool = True
    cancelled: tuple[str, ...] = ()


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
    preflight: Callable[[], None] = lambda: None
    finalizer: Callable[[str], None] = lambda _cid: None
    goal_lines: Callable[[], Sequence[str]] = lambda: ()
    #: Domains of active Goals; used only as a ranking tie-break (never lifts an ineligible Alert).
    goal_domains: Callable[[], Collection[str]] = lambda: ()
    input_version: str = ""
    subjects_for: Callable[[str, str], Sequence[str]] | None = None
    subject_labels: Mapping[str, str] = field(default_factory=dict)
    #: §10.1 disclosure record sink: one entry per model request, never the prompt itself.
    model_log: Callable[[dict[str, str]], None] = lambda _entry: None
    provider_label: str = "unconfigured"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Run:
    """One Catch-up execution; each ``_stage_*`` method is one journaled stage."""

    def __init__(self, request: CatchupRequest, deps: Deps):
        self.request, self.deps, self.led = request, deps, deps.ledger
        self.now = deps.now()
        self.failures: list[FailureClass] = []
        self._pending: list[PendingRequest] = []
        self._incumbent: IncumbentOutput | None = None
        self.done = set(self.led.committed_stages(request.catchup_id))
        self.versions = {"policy": request.policy_version, "pack": deps.pack.version,
                         "schema": str(request.schema_version),
                         "runtime": deps.input_version,
                         "ledger_schema": str(self.led.schema_version()),
                         "request": hashlib.sha256(json.dumps(request.to_dict(), sort_keys=True).encode()).hexdigest()}
        for row in self.led.conn.execute("SELECT input_versions FROM stage_journal WHERE catchup_id=?",
                                         (request.catchup_id,)):
            if json.loads(row[0]) != self.versions:
                raise LedgerError("resume input versions changed; start a new Catch-up ID")

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
            self.led.set_coverage(name, "unknown", CoverageStatus.UNAVAILABLE.value,
                                  catchup_id=self.request.catchup_id)
            return []
        if got.error is not None:
            self.failures.append(got.error)
        self.led.set_coverage(got.connector, got.account_scope, got.status.value, got.blind_spots,
                              completed_at=_iso(self.now) if got.status is CoverageStatus.COMPLETE else None,
                              catchup_id=self.request.catchup_id)
        created: list[str] = []
        for upstream in got.cancelled:
            self.led.cancel_calendar(got.connector, got.account_scope, upstream)
        for rec in got.records:
            chmac = ids.content_hmac(d.id_key, rec.text)
            if self.led.is_tombstoned(got.connector, got.account_scope, rec.upstream_id, chmac):
                continue
            known = (self.led.source_content(got.connector, got.account_scope, rec.upstream_id,
                                             rec.provider_version) if rec.provider_version else None)
            token = ids.version_token(rec.provider_version, chmac, known)
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
            if rec.calendar is not None:
                self.led.put_calendar(got.connector, got.account_scope, rec.upstream_id, sid, rec.calendar)
            if rec.incomplete:
                self.led.record_failure(sid, "content", FailureClass.DECODE.value)
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
                self.led.record_failure(sid, "normalize", FailureClass.DECODE.value)
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
        subjects = d.subjects_for(row["connector"], text) if d.subjects_for else d.subjects
        if not subjects:
            self.led.record_failure(sid, "extract", FailureClass.PERMISSION.value)
            self.failures.append(FailureClass.PERMISSION)
            return False
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        try:
            authorize_source(self.led, subjects, self.led._consent_subjects,
                             f"{row['connector']}:{row['account_scope']}", d.viewer)
        except PermissionDenied:
            self.led.record_failure(sid, "extract", FailureClass.PERMISSION.value)
            self.failures.append(FailureClass.PERMISSION)
            return False
        klass = d.source_class.get(row["connector"], row["connector"])
        candidates: list[ex.Candidate] = []
        pending_count = len(self._pending)
        complete = True
        for start in range(0, len(text), 7600):  # overlap retains sentences crossing an excerpt boundary
            signal = ex.NormalizedSignal(sid, row["connector"], text[start:start + 8000], klass,
                                         row["occurred_at"] or row["observed_at"])
            parsed = self._extract_chunk(signal, subjects)
            if parsed is None:
                complete = False
                continue
            complete = complete and parsed.complete and not parsed.rejections
            for candidate in parsed.candidates:
                candidates.append(ex.Candidate(candidate.predicate, candidate.value, candidate.subject_id,
                    sid, (candidate.span[0] + start, candidate.span[1] + start), candidate.quote,
                    obligation_ref=candidate.obligation_ref))
        if len(self._pending) > pending_count:
            return False
        if not complete:
            self.led.record_failure(sid, "extract", FailureClass.EXTRACT.value)
            self.failures.append(FailureClass.EXTRACT)
        else:
            self.led.clear_failure(sid, "extract")
        origin = "|".join(self._upstream(sid))
        for candidate in candidates:
            peers = [c for c in candidates if (c.predicate, c.subject_id) ==
                     (candidate.predicate, candidate.subject_id)]
            if len({str(c.value) for c in peers}) > 1 and any(not c.obligation_ref for c in peers):
                self.led.record_failure(sid, "extract", FailureClass.EXTRACT.value)
                self.failures.append(FailureClass.EXTRACT)
                return False  # independent obligations need explicit identity, never date-as-identity
        unique = {(c.predicate, c.subject_id, str(c.value), c.span): c for c in candidates}
        self.led.put_candidates(sid, [{
            "predicate": c.predicate,
            "value": c.value.isoformat() if hasattr(c.value, "isoformat") else c.value,
            "subject_id": c.subject_id, "span": list(c.span), "quote": c.quote,
            "obligation_key": ids.evidence_id(d.id_key, f"{origin}|{c.subject_id}|{c.predicate}|{c.obligation_ref}")}
            for c in unique.values()])
        self.led.set_processing_state(sid, ProcessingState.EXTRACTED)
        return True

    def _extract_chunk(self, signal: ex.NormalizedSignal, subjects: Sequence[str]) -> ex.ExtractionResult | None:
        from catchup_core.rule_of_two import Broker  # noqa: PLC0415
        request = ex.build_request(self.deps.pack, signal, subjects)
        Broker().check("extractor", tools=request.tools, private_data=True, untrusted_data=True)
        self.deps.model_log({
            "at": _iso(self.now), "catchup_id": self.request.catchup_id, "purpose": "extract",
            "provider": self.deps.provider_label, "data_class": self.deps.pack.confidentiality.value,
            "retention": "prompt not persisted; answer validated then discarded (policy v1)",
            "request_id": ids.evidence_id(self.deps.id_key, request.prompt)})
        try:
            output = self.deps.extractor(request.prompt)
        except ExtractionPending as pending:
            self._pending.append(pending.request)
            return None
        except Exception:
            output = ""
        parsed = ex.parse_output(output, self.deps.pack, signal, subjects)
        if parsed.rejections:
            self.failures.append(FailureClass.SCHEMA_REJECTED)
        return parsed if parsed.ok else ex.ExtractionResult((), complete=False)

    def _stage_extract(self) -> tuple[str, ...]:
        done = tuple(r["signal_id"] for r in self.led.signals_in_state(ProcessingState.NORMALIZED)
                     if self._extract_one(r))
        if self._pending:                            # stage stays uncommitted; resume re-enters it
            raise AwaitingExtraction(self.request.catchup_id, self._pending)
        return done

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
                    row["signal_id"], (payload["span"][0], payload["span"][1]), payload["quote"],
                    obligation_key=payload.get("obligation_key", "")))
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
        result = triage(
            self.led, self.deps.pack, self.now, viewer=self.deps.viewer, freshness=freshness,
            calendar=(*self.led.calendar_for(self.deps.viewer), *self.deps.calendar()),
            obligation_states=self.deps.obligation_states(), goal_domains=self.deps.goal_domains(),
            connector_of=self._connector_of,
            source_class_of=lambda sid: self.deps.source_class.get(self._connector_of(sid), self._connector_of(sid)))
        for alert in result.alerts:
            if alert.severity is not None:
                self.led.upsert_alert(alert.obligation_key, domain=alert.domain,
                    primary_subject_id=alert.subject_id, severity=alert.severity.value,
                    deadline=alert.deadline.isoformat() if alert.deadline else None,
                    latest_useful_action=alert.latest_useful_action.isoformat() if alert.latest_useful_action else None,
                    fact_ids=(alert.fact_id,))
        return result

    def _evidence_for(self, result: TriageResult) -> dict[str, list[tuple[str, str, str, str]]]:
        out: dict[str, list[tuple[str, str, str, str]]] = {}
        for a in result.alerts + result.horizon + result.supporting:
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
        for finding in result.cross_domain:
            for sid in finding.signal_ids:
                row = self.led.signal_row(sid)
                if row is not None:
                    out.setdefault(finding.obligation_key, []).append((
                        f"evidence:{row['evidence_blob_ref']}", row["connector"],
                        row["observed_at"], row["account_scope"]))
        return out

    def _coverage(self) -> list[CoverageEntry]:
        d = self.deps
        entries = [
            CoverageEntry(r["connector"], r["account_scope"],
                          CoverageStatus(r["status"]) if r["catchup_id"] == self.request.catchup_id
                          else CoverageStatus.UNAVAILABLE,
                          required=next((s.required for s in d.pack.sources if s.connector == r["connector"]), False),
                          last_verified=r["completed_at"], blind_spots=tuple(json.loads(r["blind_spots"])))
            for r in self.led.coverage_rows()
        ]
        present = {e.connector for e in entries}
        for source in d.pack.sources:
            if source.required and source.connector not in present:
                entries.append(CoverageEntry(source.connector, "unconnected", CoverageStatus.UNAVAILABLE, True,
                                             detail="no fetch result recorded"))
        entries.extend(self._incumbent_output().coverage)
        return entries

    def _calendar_line(self, coverage: Sequence[CoverageEntry]) -> str:
        """One line, no titles: how many events in the next 7 days, or that the calendar is unverified."""
        rows = [c for c in coverage if c.connector == "calendar"]
        if not rows:
            return ""
        if not all(c.status.quiet_claim_allowed for c in rows):
            return "Calendar: not verified this time — last known events may be out of date."
        today = self.now.date()
        horizon = today + timedelta(days=7)
        days = sorted({(e.start.date() if isinstance(e.start, datetime) else e.start)
                       for e in self.led.calendar_for(self.deps.viewer)
                       if today <= (e.start.date() if isinstance(e.start, datetime) else e.start) <= horizon})
        if not days:
            return "Calendar: nothing in the next 7 days."
        return f"Calendar: {len(days)} day(s) with events in the next 7 days; next {days[0]:%a %b} {days[0].day}."

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
        self.deps.preflight()
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
        has_gaps = bool(gaps) or unresolved > 0 or bool(triaged.uncertain)
        line = self.deps.coverage_line()

        conclusions = self._incumbent_output().conclusions
        legacy = [c.text for c in conclusions if c.kind != "open_item"]
        open_items = [OpenItemLine(c.text, _parse_day(c.due)) for c in conclusions if c.kind == "open_item"]
        goals = self.deps.goal_lines()
        fmt = req.briefing_format
        cal_line = self._calendar_line(coverage)
        def build(evidence: Mapping[str, list[tuple[str, str, str, str]]]) -> Briefing:
            return compose(self.now, triaged, evidence_for=evidence, coverage_line=line, coverage_gaps=gaps,
                           unresolved_signals=unresolved, legacy_lines=legacy, goal_lines=goals,
                           subject_labels=self.deps.subject_labels, fmt=fmt, open_items=open_items,
                           calendar_line=cal_line)
        briefing = build(self._evidence_for(triaged))
        self.stage(Stage.COMPOSE, lambda: ())
        problems = validate_briefing(briefing.text, briefing.evidence, has_gaps=has_gaps)
        if problems:                                 # one safe retry: citation-dependent claims become MISSING
            briefing = build({})
            problems = validate_briefing(briefing.text, briefing.evidence, has_gaps=has_gaps)
        if problems:
            self.failures.append(FailureClass.POLICY)
            return CatchupResult(
                req.catchup_id, req.mode, RunStatus.BLOCKED, coverage=tuple(coverage),
                reason_unpublished="Briefing failed validation: " + "; ".join(problems[:3]),
                failures=tuple(dict.fromkeys(self.failures)))
        self.stage(Stage.VALIDATE, lambda: ())

        saved = self._persist_draft(triaged, briefing)
        text = str(saved["text"]) if saved else briefing.text
        briefing_id, version = self._commit(text)
        self.stage(Stage.PRESENTED, lambda: ())
        def finalize() -> tuple[str, ...]:
            self.deps.finalizer(req.catchup_id)
            return ()
        self.stage(Stage.FINALIZED, finalize)

        failures = tuple(dict.fromkeys(self.failures))
        status = CatchupResult.derive_status(tuple(coverage), failures)
        if (unresolved or triaged.uncertain) and status is RunStatus.COMPLETE:
            status = RunStatus.DEGRADED
        return CatchupResult(
            req.catchup_id, req.mode, status, coverage=tuple(coverage), briefing_id=briefing_id,
            briefing_version=version, reason_unpublished=None if briefing_id else "shadow run",
            failures=failures, artifact_ids=tuple(a.fact_id for a in triaged.alerts), briefing_text=text)

    def _persist_draft(self, triaged: TriageResult, briefing: Briefing) -> dict[str, object] | None:
        """Pin the composed text to the exact Fact versions and permission generation it was built from.

        A resumed Catch-up re-reads the saved draft; if evidence or permissions moved meanwhile, it must
        start over instead of publishing text that no longer matches what is true.
        """
        req, led = self.request, self.led
        fact_ids = list({a.fact_id for a in triaged.alerts + triaged.horizon + triaged.supporting})
        versions = {fid: led.get_fact(fid, self.deps.viewer).recorded_at for fid in fact_ids}
        saved = led.draft(req.catchup_id, {"text": briefing.text, "fact_ids": fact_ids,
                                         "fact_versions": versions,
                                         "permission_generation": led._permission_generation()})
        if saved and saved.get("permission_generation", 0) != led._permission_generation():
            raise LedgerError("permissions changed since composition; start a new Catch-up")
        for fid in (saved or {}).get("fact_ids", []):
            current = led.get_fact(fid, self.deps.viewer)
            if current.recorded_at != (saved or {}).get("fact_versions", {}).get(fid):
                raise LedgerError("evidence changed after composition; start a new Catch-up")
        with led.transaction() as db:
            for fid in fact_ids:
                db.execute("INSERT OR IGNORE INTO briefing_facts VALUES(?,?)", (req.catchup_id, fid))
            for ref in briefing.evidence:
                db.execute("INSERT OR IGNORE INTO briefing_signals SELECT ?,signal_id FROM signals "
                           "WHERE evidence_blob_ref=?", (req.catchup_id, ref.token.removeprefix("evidence:")))
        return saved

    def _commit(self, text: str) -> tuple[str | None, int | None]:
        req = self.request
        if req.mode is not ExecutionMode.AUTHORITATIVE:
            self.stage(Stage.COMMIT, lambda: ())
            return None, None
        published: list[int] = []

        def do_commit() -> tuple[str, ...]:
            if self.deps.publisher is not None:
                self.deps.publisher(req.catchup_id, text, 1)
            version, _created = self.led.publish_briefing(req.catchup_id, text)
            published.append(version)
            return (f"brf_{req.catchup_id}",)

        self.stage(Stage.COMMIT, do_commit)
        if not published:                            # resumed after COMMIT: read the version back
            published.append(self.led.publish_briefing(req.catchup_id, text)[0])
        return f"brf_{req.catchup_id}", published[0]


def run_catchup(request: CatchupRequest, deps: Deps) -> CatchupResult:
    """Run (or resume) one Catch-up and return its typed result."""
    try:
        with session_lock(deps.ledger._path.with_suffix(".session.lock")):
            deps.ledger._blob_deleter = deps.blobs.delete
            deps.ledger.resume_pending_forgets()
            return _Run(request, deps).run()
    except PermissionDenied as exc:
        return CatchupResult(request.catchup_id, request.mode, RunStatus.BLOCKED,
                             reason_unpublished=str(exc), failures=(FailureClass.PERMISSION,))
    except (OSError, LedgerError, SessionBusy, sqlite3.Error, DecryptionError, KeyUnavailableError) as exc:
        return CatchupResult(request.catchup_id, request.mode, RunStatus.BLOCKED,
                             reason_unpublished=str(exc), failures=(FailureClass.STORAGE,))


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


def _typed(kind: str, raw: object) -> object:
    from datetime import date  # noqa: PLC0415
    if kind == "date":
        return date.fromisoformat(str(raw))
    if kind == "datetime":
        return datetime.fromisoformat(str(raw))
    return raw
