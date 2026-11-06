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

import contextlib
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from catchup_core import extract as ex
from catchup_core import ids
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.compose import Briefing, ContextClaim, ContextText, OpenItemLine, compose, validate_briefing
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
from catchup_core.exchange import AwaitingExtraction, ExchangeExtractor, ExtractionPending, PendingRequest
from catchup_core.incumbent import GuardedIncumbent, IncumbentOutput
from catchup_core.ledger import Ledger, LedgerError, PermissionDenied, SignalInput, Suppressed
from catchup_core.reconcile import reconcile
from catchup_core.resources import AdmissionStore, PolicyRequired, ResourceLimit
from catchup_core.session_lock import SessionBusy, session_lock
from catchup_core.triage import AlertDraft, CalendarEvent, TriageResult, triage

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
    document_method: str | None = None

    def __post_init__(self) -> None:
        if self.document_method is not None and self.document_method not in ("text", "ocr", "unknown"):
            raise ValueError("document_method must be text, ocr or unknown")


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
    # The consumer acknowledges only after every local evidence commit succeeds.
    commit: Callable[[], None] | None = field(default=None, compare=False, repr=False)
    completed_at: str | None = None
    cursor: str | None = None
    cursor_reset: bool = False
    deleted_collections: tuple[str, ...] = ()
    # (collection, window start, window end): that collection's listing was complete; None bounds = unbounded.
    full_listings: tuple[tuple[str, str | None, str | None], ...] = ()
    # Set only by runtime after an authorized provider fetch under this consent generation.
    provider_permission_generation: int | None = None


class StageFault(RuntimeError):
    """Raised by fault-injection hooks to simulate a crash at a stage boundary."""


@dataclass(frozen=True)
class ContextLines:
    """A permission-filtered section plus explicit limits on its coverage."""
    lines: Sequence[str] = ()
    gaps: Sequence[str] = ()
    fact_versions: Mapping[str, str] = field(default_factory=dict)
    citations: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    commitment_count: int | None = None


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
    reminder_proposer: Callable[[Sequence[AlertDraft]], object] | None = None
    publisher: Callable[[str, str, int], None] | None = None
    fault: Callable[[Stage], None] | None = None
    coverage_line: Callable[[], str] = lambda: ""  # Optional trusted label; default summarizes this run's coverage.
    preflight: Callable[[], None] = lambda: None
    finalizer: Callable[[str], None] = lambda _cid: None
    # Core-owned Open Items file; authoritative runs only.
    items_publisher: Callable[[Sequence[AlertDraft]], object] | None = None
    goal_lines: Callable[[], Sequence[str] | ContextLines] = lambda: ()
    plan_lines: Callable[[], Sequence[str] | ContextLines] = lambda: ()
    responsibility_lines: Callable[[], Sequence[str] | ContextLines] = lambda: ()
    coordination_lines: Callable[[], Sequence[str] | ContextLines] = lambda: ()
    #: Domains of active Goals; used only as a ranking tie-break (never lifts an ineligible Alert).
    goal_domains: Callable[[], Collection[str]] = lambda: ()
    input_version: str = ""
    subjects_for: Callable[[str, str], Sequence[str]] | None = None
    #: Strictest subject set recorded for access checks when ``subjects_for`` names nobody; never grants extraction.
    access_subjects_for: Callable[[str, str], Sequence[str]] | None = None
    subject_labels: Mapping[str, str] = field(default_factory=dict)
    scope_subjects: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: Configured account per source; retained rows for replaced accounts are history, not current coverage.
    expected_scopes: Mapping[str, str] = field(default_factory=dict)
    #: §10.1 disclosure record sink: one entry per model request, never the prompt itself.
    model_log: Callable[[dict[str, str]], None] = lambda _entry: None
    provider_label: str = "unconfigured"
    mode: ExecutionMode = ExecutionMode.AUTHORITATIVE
    #: Per-run performance record sink (status, seconds, stage seconds, model requests); never content.
    metrics: Callable[[dict[str, object]], None] = lambda _entry: None
    perf: Callable[[], float] = time.monotonic
    #: Trace sink (OTLP/JSON-shaped, content-free); receives the same entry as ``metrics``.
    trace: Callable[[dict[str, object]], None] = lambda _entry: None
    #: Keyed generation of all curated inputs; checked before publication, including inputs omitted in prose.
    context_version: Callable[[], str] = lambda: ""
    #: Current runtime policy/code generation, checked before stages, fetches, disclosures and publication.
    validate_inputs: Callable[[], None] = lambda: None
    inspection_preflight: Callable[[], None] | None = None
    #: One installation-wide fence shared with complete checkpoints and action dispatch.
    maintenance: Callable[[], contextlib.AbstractContextManager[Any]] = contextlib.nullcontext
    evaluation_cohort: Mapping[str, str] = field(default_factory=dict)
    extractor_identity_matched: bool = False
    admissions: AdmissionStore | None = None
    require_resource_policy: bool = False


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aware(moment: datetime) -> datetime:
    """A source stamp without an offset (a calendar date, say) is read as UTC rather than failing the comparison."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class _Run:
    """One Catch-up execution; each ``_stage_*`` method is one journaled stage."""

    def __init__(self, request: CatchupRequest, deps: Deps, metric_sequence: int):
        deps.validate_inputs()
        self.request, self.deps, self.led = request, deps, deps.ledger
        self.metric_sequence = metric_sequence
        self.now = deps.now()
        self.failures: list[FailureClass] = []
        self.provider_signal_generations: dict[str, int] = {}
        self._pending: list[PendingRequest] = []
        self._incumbent: IncumbentOutput | None = None
        self.done = set(self.led.committed_stages(request.catchup_id))
        self.timings: dict[str, float] = {}
        self.model_requests = 0
        self.prompt_chars = 0
        self.answer_chars = 0
        self.extraction_rejections: dict[str, int] = {}
        self.context_version = deps.context_version()
        self.saved_draft: dict[str, object] | None = None
        self.context_facts: dict[str, str] = {}
        self.context_generation: int | None = None
        self.versions = {"policy": request.policy_version, "pack": deps.pack.version,
                         "evaluation": hashlib.sha256(
                             json.dumps(dict(deps.evaluation_cohort), sort_keys=True).encode()).hexdigest(),
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
        started = self.deps.perf()
        try:
            self.deps.validate_inputs()
            artifacts = body()
        finally:
            self.timings[name.value] = round(self.deps.perf() - started, 3)
        self.led.record_stage(StageCheckpoint(self.request.catchup_id, name, "committed", self.versions, artifacts))
        self.done.add(name.value)

    # ── FETCH ────────────────────────────────────────────────────────────────
    def _fetch_one(self, name: str, fetcher: Callable[[], ConnectorFetch]) -> list[str]:
        d = self.deps
        d.validate_inputs()
        try:
            got = fetcher()
        except Exception:  # degrade only this connector's scope
            self.failures.append(FailureClass.PROVIDER)
            self.led.set_coverage(name, "unknown", CoverageStatus.UNAVAILABLE.value,
                                  catchup_id=self.request.catchup_id)
            return []
        with self.led.transaction():
            created = self._store_fetch(got, name)
        if got.commit is not None:
            got.commit()
        return created

    def _store_fetch(self, got: ConnectorFetch, name: str) -> list[str]:  # noqa: PLR0912
        d = self.deps
        self.led.set_source_subjects(got.connector, got.account_scope, d.scope_subjects.get(name, d.subjects))
        if got.error is not None:
            self.failures.append(got.error)
        created: list[str] = []
        observed: list[str] = []
        calendars: list[tuple[ConnectorRecord, str]] = []
        for upstream in got.cancelled:
            self.led.cancel_calendar(got.connector, got.account_scope, upstream)
        for rec in got.records:
            chmac = self._source_hmac(got, rec)
            if chmac is None:
                continue
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
            access = d.access_subjects_for or d.subjects_for
            self.led.set_signal_subjects(sid, access(got.connector, rec.text) if access else d.subjects)
            if got.connector == "documents":
                self.led.set_document_method(sid, rec.document_method or "unknown")
            if is_new:
                created.append(sid)
            observed.append(sid)
            if rec.calendar is not None:
                calendars.append((rec, sid))
            if rec.incomplete:
                self.led.record_failure(sid, "content", FailureClass.DECODE.value)
            else:
                self.led.clear_failure(sid, "content")
        self.led.observe_source_versions(observed, complete=got.status is CoverageStatus.COMPLETE
                                         and not any(r.incomplete for r in got.records))
        for coll_id in got.deleted_collections:
            self.led.cancel_calendar_collection(got.connector, got.account_scope, coll_id)
        self._check_cancelled_versions(got, observed)
        self._materialize_calendars(got, calendars)
        if got.status is CoverageStatus.COMPLETE and not got.cursor_reset:
            listed = {rec.upstream_id for rec, _ in calendars}
            for coll_id, window_start, window_end in got.full_listings:
                self.led.withdraw_unlisted_calendar_occurrences(
                    got.connector, got.account_scope, coll_id, listed, window_start, window_end)
        cursor_to_commit: str | None = None
        if got.cursor_reset:
            reset_cursor = True
        else:
            reset_cursor = False
            if got.cursor and got.status is CoverageStatus.COMPLETE:
                cursor_to_commit = got.cursor
        self.led.set_coverage(got.connector, got.account_scope, got.status.value, got.blind_spots,
                              completed_at=(got.completed_at or _iso(self.now))
                              if got.status is CoverageStatus.COMPLETE else None,
                              catchup_id=self.request.catchup_id,
                              cursor_generation=cursor_to_commit,
                              reset_cursor=reset_cursor)
        self._record_provider_generations(got.provider_permission_generation, observed)
        return created

    def _record_provider_generations(self, generation: int | None, observed: list[str]) -> None:
        if (type(generation) is int and generation >= 0
                and generation == self.led._permission_generation()):
            for sid in observed:
                if self._provider_signal_current(sid, self.deps.viewer):
                    self.provider_signal_generations[sid] = generation

    def _provider_signal_current(self, sid: str, viewer: str) -> bool:
        if self.led.source_is_obsolete(sid):
            return False
        try:
            self.led.require_signal_operation(sid, viewer)
        except PermissionDenied:
            return False
        return True

    def _source_hmac(self, got: ConnectorFetch, rec: ConnectorRecord) -> str | None:
        chmac = ids.content_hmac(self.deps.id_key, rec.text)
        if rec.calendar is None:
            return chmac
        # Preserve pre-upgrade erasure while adding calendar metadata to immutable source identity.
        if self.led.is_tombstoned(got.connector, got.account_scope, rec.upstream_id, chmac):
            return None
        event = rec.calendar
        material = [rec.text, event.event_id, event.start.isoformat(), event.end.isoformat(), event.all_day, event.kind]
        return ids.content_hmac(self.deps.id_key, json.dumps(material))

    def _check_cancelled_versions(self, got: ConnectorFetch, observed: Sequence[str]) -> None:
        for sid in observed:
            row = self.led.signal_row(sid)
            if row and row['upstream_id'] in got.cancelled:
                with self.led.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO source_heads VALUES(?,?,?,NULL,0)",
                               (got.connector, got.account_scope, row['upstream_id']))
                self.led.record_failure(sid, 'version', 'extract')

    def _materialize_calendars(self, got: ConnectorFetch, calendars: Sequence[tuple[ConnectorRecord, str]]) -> None:
        for rec, sid in calendars:
            selected = self.led.conn.execute("SELECT signal_id FROM source_heads WHERE connector=? "
                "AND account_scope=? AND upstream_id=?", (got.connector, got.account_scope, rec.upstream_id)).fetchone()
            if selected and selected[0] == sid:
                self.led.put_calendar(got.connector, got.account_scope, rec.upstream_id, sid, rec.calendar)
            else:
                with self.led.transaction() as db:
                    db.execute("DELETE FROM calendar_records WHERE connector=? AND account_scope=? AND upstream_id=?",
                               (got.connector, got.account_scope, rec.upstream_id))

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
    def _extract_authorize(self, sid: str, subjects: Sequence[str]) -> sqlite3.Row:
        from catchup_core.policy import authorize_source  # noqa: PLC0415
        self.deps.validate_inputs()
        row = self.led.signal_row(sid)
        if (row is None or self.led.source_is_obsolete(sid)
                or self.led.is_tombstoned(row['connector'], row['account_scope'],
                                         row['upstream_id'], row['content_hmac'])
                or self.led.conn.execute("SELECT 1 FROM signal_failures WHERE signal_id=? AND stage='version'",
                                         (sid,)).fetchone()):
            raise PermissionDenied('source is no longer current')
        if not subjects:
            raise PermissionDenied('source has no resolved subjects')
        authorize_source(self.led, subjects, self.led._consent_subjects,
                         f"{row['connector']}:{row['account_scope']}", self.deps.viewer)
        return row

    def _extract_resource_failure(self, sid: str) -> None:
        self.led.record_failure(sid, 'resource', FailureClass.POLICY.value)
        self.failures.append(FailureClass.POLICY)

    def _extract_one(self, row: sqlite3.Row) -> bool:
        d, sid = self.deps, row["signal_id"]
        if self.led.conn.execute("SELECT 1 FROM signal_failures WHERE signal_id=? AND stage='version'",
                                 (sid,)).fetchone():
            self.led.set_processing_state(sid, ProcessingState.RECONCILED)
            return False
        text = d.blobs.get(row["evidence_blob_ref"])
        subjects = d.subjects_for(row["connector"], text) if d.subjects_for else d.subjects
        if not subjects:
            self.led.record_failure(sid, "extract", FailureClass.PERMISSION.value)
            self.failures.append(FailureClass.PERMISSION)
            return False
        try:
            self._extract_authorize(sid, subjects)
        except PermissionDenied:
            self.led.record_failure(sid, "extract", FailureClass.PERMISSION.value)
            self.failures.append(FailureClass.PERMISSION)
            return False
        klass = d.source_class.get(row["connector"], row["connector"])
        candidates: list[ex.Candidate] = []
        incumbent_candidates: list[dict[str, object]] = []
        pending_count = len(self._pending)
        self.led.clear_failure(sid, 'resource')
        complete = True
        for start in range(0, len(text), 7600):  # overlap retains sentences crossing an excerpt boundary
            signal = ex.NormalizedSignal(sid, row["connector"], text[start:start + 8000], klass,
                                         row["occurred_at"])
            try:
                parsed = self._extract_chunk(signal, subjects)
            except PermissionDenied:
                self.led.record_failure(sid, 'extract', FailureClass.PERMISSION.value)
                self.failures.append(FailureClass.PERMISSION)
                return False
            if parsed is None:
                complete = False
                continue
            complete = complete and parsed.complete and not parsed.rejections
            for other in parsed.other_obligations:
                a, b = other["span"]
                span = _rebase_span(text, start, a, b, row['span_unit'])
                incumbent_candidates.append({**other, "span": list(span)})
            for candidate in parsed.candidates:
                a, b = candidate.span
                span = _rebase_span(text, start, a, b, row['span_unit'])
                candidates.append(ex.Candidate(candidate.predicate, candidate.value, candidate.subject_id,
                    sid, span, candidate.quote,
                    obligation_ref=candidate.obligation_ref))
        if (len(self._pending) > pending_count or self.led.conn.execute(
            "SELECT 1 FROM signal_failures WHERE signal_id=? AND stage='resource'", (sid,)).fetchone()):
            return False
        if not complete:
            self.led.record_failure(sid, "extract", FailureClass.EXTRACT.value)
            self.failures.append(FailureClass.EXTRACT)
        else:
            self.led.clear_failure(sid, "extract")
        origin = "|".join(self._upstream(sid))
        ambiguous = {(c.predicate, c.subject_id) for c in candidates
                     if len({str(p.value) for p in candidates
                             if (p.predicate, p.subject_id) == (c.predicate, c.subject_id)}) > 1
                     and any(not p.obligation_ref for p in candidates
                             if (p.predicate, p.subject_id) == (c.predicate, c.subject_id))}
        if ambiguous:
            # Independent obligations need explicit identity, never date-as-identity. Only the ambiguous
            # group is withheld (and the Signal is marked failed); unrelated valid candidates survive.
            self.led.record_failure(sid, "extract", FailureClass.EXTRACT.value)
            self.failures.append(FailureClass.EXTRACT)
            candidates = [c for c in candidates if (c.predicate, c.subject_id) not in ambiguous]
        unique = {(c.predicate, c.subject_id, str(c.value), c.span, c.obligation_ref): c for c in candidates}
        self.led.put_candidates(sid, [{
            "predicate": c.predicate,
            "value": c.value.isoformat() if hasattr(c.value, "isoformat") else c.value,
            "subject_id": c.subject_id, "span": list(c.span), "quote": c.quote,
            "obligation_key": ids.evidence_id(d.id_key,
                f"{_obligation_origin(c.obligation_ref, text, origin)}|{c.subject_id}|"
                f"{link_predicate(d.pack, c.predicate)}")}
            for c in unique.values()] + incumbent_candidates)
        self.led.set_processing_state(sid, ProcessingState.EXTRACTED)
        return True

    def _extract_chunk(  # noqa: PLR0912, PLR0915
        self, signal: ex.NormalizedSignal, subjects: Sequence[str],
    ) -> ex.ExtractionResult | None:
        from catchup_core.rule_of_two import Broker  # noqa: PLC0415
        d, sid = self.deps, signal.signal_id
        request = ex.build_request(d.pack, signal, subjects)
        Broker().check("extractor", tools=request.tools, private_data=True, untrusted_data=True)
        store = d.admissions
        output = None
        cached = False
        request_key = ''
        try:
            row = self._extract_authorize(sid, subjects)
            if store is None and d.require_resource_policy:
                raise PolicyRequired('explicit resource policy required before extraction')
            if store is not None:
                material = [sid, row['upstream_version'], row['content_hmac'], row['span_unit'],
                            request.prompt, dict(d.evaluation_cohort),
                            d.viewer, sorted(subjects), self.led._permission_generation()]
                request_key = ids.evidence_id(d.id_key, json.dumps(material, sort_keys=True, separators=(',', ':')))
                output = store.cached_output(self.request.catchup_id, request_key)
                cached = output is not None
            supplied = isinstance(d.extractor, ExchangeExtractor) and d.extractor.has_response(request.prompt)
            if output is None:
                prompt_bytes = len(request.prompt.encode('utf-8'))
                if store is not None:
                    if not supplied and (
                            getattr(self, '_extract_disclosures', 0) >= store.policy.max_requests_per_bundle
                            or getattr(self, '_extract_prompt_bytes', 0) + prompt_bytes
                            > store.policy.max_prompt_bytes_per_bundle):
                        raise ResourceLimit('invocation disclosure allocation exhausted')
                    admitted = store.admit(self.request.catchup_id, sid, request_key, prompt_bytes, d.now(),
                                           bundle_id=f'{self.request.catchup_id}:{self.metric_sequence}',
                                           supplied=supplied)
                    if not admitted and not isinstance(d.extractor, ExchangeExtractor):
                        raise ResourceLimit('prior request has no validated output; effect is uncertain')
                self._extract_authorize(sid, subjects)
                if store is not None:
                    store.require_active(self.request.catchup_id)
                if not supplied:
                    self._extract_disclosures = getattr(self, '_extract_disclosures', 0) + 1
                    self._extract_prompt_bytes = getattr(self, '_extract_prompt_bytes', 0) + prompt_bytes
                    self.model_requests += 1
                    self.prompt_chars += len(request.prompt)
                    d.model_log({
                        'at': _iso(self.now), 'catchup_id': self.request.catchup_id, 'purpose': 'extract',
                        'provider': d.provider_label, 'data_class': d.pack.confidentiality.value,
                        'retention': ('validated answer encrypted in resource cache' if store is not None
                                      else 'prompt not persisted; answer validated then discarded (policy v1)'),
                        'request_id': ids.evidence_id(d.id_key, request.prompt)})
                output = d.extractor(request.prompt)
        except ExtractionPending as pending:
            self._pending.append(pending.request)
            return None
        except (PolicyRequired, ResourceLimit):
            self._extract_resource_failure(sid)
            return None
        except PermissionDenied:
            raise
        except Exception:
            output = ""
        self._extract_authorize(sid, subjects)
        self.answer_chars += len(output)
        try:
            parsed = ex.parse_output(output, d.pack, signal, subjects)
        except Exception:        # one unreadable answer fails this Signal only; siblings keep their work
            return ex.ExtractionResult((), complete=False)
        if parsed.rejections:
            self.failures.append(FailureClass.SCHEMA_REJECTED)
            for rejection in parsed.rejections:             # fixed reason text only, never quoted content
                reason = rejection.reason.split(":", 1)[0]
                self.extraction_rejections[reason] = self.extraction_rejections.get(reason, 0) + 1
        if store is not None and not cached and parsed.ok and parsed.complete and not parsed.rejections:
            try:
                store.cache_output(self.request.catchup_id, sid, request_key, output, d.now())
            except (PolicyRequired, ResourceLimit):
                self._extract_resource_failure(sid)
                return None
        return parsed if parsed.ok else ex.ExtractionResult((), complete=False)

    def _stage_extract(self) -> tuple[str, ...]:
        for row in self.led.conn.execute("SELECT signal_id FROM signal_failures WHERE stage='extract' "
                                         "AND retry_eligible=1 AND attempts<3").fetchall():
            self.led.set_processing_state(row[0], ProcessingState.NORMALIZED)
        raw_rows = (self.deps.admissions.select_pending(current_catchup_id=self.request.catchup_id)
            if self.deps.admissions is not None else self.led.signals_in_state(ProcessingState.NORMALIZED))
        rows = fair_backlog_slice(raw_rows, limit=500)
        done = tuple(r["signal_id"] for r in rows
                     if self._extract_one(r))
        if self.deps.admissions is not None and self.led.conn.execute(
                "SELECT 1 FROM signals WHERE processing_state='normalized' LIMIT 1").fetchone():
            self.failures.append(FailureClass.POLICY)
        if self._pending:                            # stage stays uncommitted; resume re-enters it
            raise AwaitingExtraction(self.request.catchup_id, self._pending)
        return done

    # ── RECONCILE ────────────────────────────────────────────────────────────
    def _source_class(self, signal_id: str) -> str:
        row = self.led.signal_row(signal_id)
        connector = row["connector"] if row else ""
        if connector == "documents" and self.led.document_method(signal_id) != "text":
            return "document_review_needed"
        return self.deps.source_class.get(connector, connector)

    def _upstream(self, signal_id: str) -> tuple[str, str, str]:
        row = self.led.signal_row(signal_id)
        return (row["connector"], row["account_scope"], row["upstream_id"]) if row else ("", "", signal_id)

    def _stage_reconcile(self) -> tuple[str, ...]:
        cands: list[ex.Candidate] = []
        rows = self.led.signals_in_state(ProcessingState.EXTRACTED)
        for row in rows:
            if self.led.source_is_obsolete(row["signal_id"]) or self.led.conn.execute(
                    "SELECT 1 FROM signal_failures WHERE signal_id=? AND stage='version'",
                    (row["signal_id"],)).fetchone():
                continue
            for payload in self.led.candidates_for(row["signal_id"]):
                if payload.get("kind") == "incumbent":
                    continue
                kind = self.deps.pack.predicates[payload["predicate"]].type
                cands.append(ex.Candidate(
                    payload["predicate"], _typed(kind, payload["value"]), payload["subject_id"],
                    row["signal_id"], (payload["span"][0], payload["span"][1]), payload["quote"],
                    obligation_key=payload.get("obligation_key", "")))
        result = reconcile(self.led, self.deps.pack, cands, observed_at=_iso(self.now),
                           source_class_of=self._source_class, upstream_key_of=self._upstream,
                           rule_version=self.request.policy_version)
        for change in result.changes:
            self.led.mark_document_field_fact(change.fact_id)
        for row in rows:
            if not self.led.conn.execute("SELECT 1 FROM signal_failures WHERE signal_id=?",
                                         (row['signal_id'],)).fetchone():
                self.led.replace_source_support(row['signal_id'])
            self.led.set_processing_state(row["signal_id"], ProcessingState.RECONCILED)
        return tuple(r["signal_id"] for r in rows) + tuple(c.fact_id for c in result.changes)

    # ── TRIAGE / COMPOSE / VALIDATE ──────────────────────────────────────────
    def _connector_of(self, signal_id: str) -> str:
        row = self.led.signal_row(signal_id)
        return str(row["connector"]) if row else ""

    def _triage(self) -> TriageResult:
        freshness = {}
        for row in self.led.conn.execute("SELECT s.signal_id,s.occurred_at,s.observed_at,c.completed_at "
                "FROM signals s JOIN connector_coverage c ON s.connector=c.connector "
                "AND s.account_scope=c.account_scope"):
            # An incremental fetch does not re-verify older documentary evidence.
            stamp = row["occurred_at"] or row["observed_at"]
            if row["completed_at"]:
                freshness[row["signal_id"]] = min(_aware(datetime.fromisoformat(s.replace("Z", "+00:00")))
                    for s in (stamp, row["completed_at"]))
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
        for fid in self.context_facts:
            fact = self.led.get_fact(fid, self.deps.viewer)
            refs = []
            for sid, _start, _end in fact.evidence:
                source = self.led.signal_row(sid)
                if source is not None:
                    refs.append((f"evidence:{source['evidence_blob_ref']}", str(source['connector']),
                                 str(source['observed_at']), str(source['account_scope'])))
            if not refs and fact.provenance_class == 'user_confirmed':
                refs = [(f'fact:{fid}@{fact.recorded_at}', 'Explicit confirmation', fact.recorded_at, fact.domain)]
            out[f'fact:{fid}'] = refs
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
            for r in self.led.visible_coverage(d.viewer)
            if not d.expected_scopes or d.expected_scopes.get(r['connector']) == r['account_scope']
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
        unattributed = self._unattributed_events("calendar")
        note = f" {unattributed} calendar event(s) not attributed to a person." if unattributed else ""
        today = self.now.date()
        horizon = today + timedelta(days=7)
        days = sorted({(e.start.date() if isinstance(e.start, datetime) else e.start)
                       for e in self.led.calendar_for(self.deps.viewer)
                       if today <= (e.start.date() if isinstance(e.start, datetime) else e.start) <= horizon})
        if not days:
            return f"Calendar: nothing in the next 7 days.{note}"
        return f"Calendar: {len(days)} day(s) with events in the next 7 days; next {days[0]:%a %b} {days[0].day}.{note}"

    def _sources_without_mail(self) -> list[str]:
        """Mail sources that have never delivered a Signal: a complete fetch of nothing is not a verified quiet."""
        seen = {row[0] for row in self.led.conn.execute("SELECT DISTINCT connector FROM signals")}
        return sorted({s.connector for s in self.deps.pack.sources
                       if "calendar" not in s.connector and s.connector in self.deps.fetchers
                       and s.connector not in seen})

    def _unattributed_events(self, connector: str) -> int:
        """Signals set aside because no single household person is named; counts only, never content."""
        row = self.led.conn.execute(
            "SELECT count(DISTINCT s.signal_id) FROM signals s JOIN signal_failures f ON f.signal_id=s.signal_id "
            "WHERE s.connector=? AND f.stage='extract' AND f.error_class=?",
            (connector, FailureClass.PERMISSION.value)).fetchone()
        return int(row[0])

    def _incumbent_output(self) -> IncumbentOutput:
        """The guarded incumbent runs once per Catch-up (never writes, never publishes)."""
        if self._incumbent is None:
            from catchup_core.policy import authorize_source  # noqa: PLC0415
            permitted = []
            for row in self.led.signals_in_state(ProcessingState.RECONCILED):
                for payload in self.led.candidates_for(row["signal_id"]):
                    if payload.get("kind") != "incumbent":
                        continue
                    authorize_source(self.led, [payload["subject_id"]], self.led._consent_subjects,
                                     f"{row['connector']}:{row['account_scope']}", self.deps.viewer)
                    permitted.append(json.dumps({**payload, "signal_id": row["signal_id"],
                                                  "version": row["upstream_version"]}))
            self._incumbent = self.deps.incumbent.run(permitted)
            self.failures.extend(self._incumbent.failures)
        return self._incumbent

    def _context_sections(self) -> tuple[list[Sequence[ContextText]], list[str], int]:
        """Pin each authored section once, preserving unavailable and deferred coverage."""
        self.context_generation = self.led.fact_generation()
        sections: list[Sequence[ContextText]] = []
        gaps: list[str] = []
        commitments = 0
        for label, load in (("Goals", self.deps.goal_lines), ("Goal plans", self.deps.plan_lines),
                            ("Responsibilities", self.deps.responsibility_lines),
                            ("Household tasks", self.deps.coordination_lines)):
            try:
                section = load()
                if isinstance(section, ContextLines):
                    if label != 'Goals':
                        commitments += (len(section.lines) if section.commitment_count is None
                                        else section.commitment_count)
                    for fid, version in section.fact_versions.items():
                        if fid in self.context_facts and self.context_facts[fid] != version:
                            raise LedgerError('context evidence changed during composition')
                        self.context_facts[fid] = version
                    gaps.extend(section.gaps)
                    sections.append([ContextClaim(text, tuple(f'fact:{fid}' for fid in section.citations[index]))
                        if index in section.citations else text for index, text in enumerate(section.lines)])
                else:
                    sections.append(section)
                    if label != 'Goals':
                        commitments += len(section)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                gaps.append(f"{label} unavailable; commitment coverage is unknown")
                sections.append(())
        return sections, gaps, commitments

    # ── driver ───────────────────────────────────────────────────────────────
    def run(self) -> CatchupResult:  # noqa: PLR0912, PLR0915 - the ordered stage machine is intentionally explicit
        req, led = self.request, self.led
        self.stage(Stage.CREATED, lambda: ())
        try:
            self.deps.preflight()
        except (OSError, RuntimeError, ValueError) as exc:
            return CatchupResult(req.catchup_id, req.mode, RunStatus.BLOCKED,
                                 reason_unpublished=f"Preflight failed: {exc}", failures=(FailureClass.POLICY,))
        self.stage(Stage.PREFLIGHT, lambda: ())
        self.stage(Stage.FETCH, self._stage_fetch)
        self.stage(Stage.NORMALIZE, self._stage_normalize)
        self.stage(Stage.EXTRACT, self._stage_extract)
        self.stage(Stage.RECONCILE, self._stage_reconcile)
        if self.led.conn.execute("SELECT 1 FROM facts LIMIT 1").fetchone():
            self.led.record_capability("RA-02", req.catchup_id)
        if self.led.conn.execute("SELECT 1 FROM signals LIMIT 1").fetchone():
            self.led.record_capability("RA-07", req.catchup_id, succeeded=not self.led.unresolved_signals())

        triaged = self._triage()
        self.stage(Stage.TRIAGE, lambda: tuple(a.obligation_key for a in triaged.alerts))
        coverage = self._coverage()
        gaps = [f"{c.connector} {c.status.value}" + (f" ({c.detail})" if c.detail else "")
                for c in coverage if c.required and not c.status.quiet_claim_allowed]
        unresolved = len(led.unresolved_signals())
        has_gaps = bool(gaps) or unresolved > 0 or bool(triaged.uncertain)
        required = [entry for entry in coverage if entry.required]
        complete = sum(entry.status.quiet_claim_allowed for entry in required)
        line = self.deps.coverage_line() or (
            f'Coverage: {complete}/{len(required)} required source scopes complete this Catch-up.' if required
            else 'Coverage: no required source scopes configured.')
        silent = self._sources_without_mail()
        if silent:
            line += f" No mail seen from: {', '.join(silent)}."
        if self.deps.admissions is None and self.deps.require_resource_policy and led.conn.execute(
                "SELECT 1 FROM signal_failures WHERE stage='resource' LIMIT 1").fetchone():
            # extraction is held back until the owner sets resource limits: say so, never fail quietly
            line += ' Extraction: policy_needed (no resource limits configured).'
            gaps.append('extraction policy_needed (resource limits not configured)')
            has_gaps = True

        conclusions = self._incumbent_output().conclusions
        legacy = [c.text for c in conclusions if c.kind == "record"]
        incumbent_lines = [c.text for c in conclusions if c.kind == "obligation"]
        if incumbent_lines:
            unresolved += len(incumbent_lines)
            has_gaps = True
        open_items = [OpenItemLine(c.text, _parse_day(c.due)) for c in conclusions if c.kind == "open_item"]
        sections, context_gaps, commitments = self._context_sections()
        gaps.extend(context_gaps)
        has_gaps = has_gaps or bool(gaps)
        fmt = req.briefing_format
        cal_line = self._calendar_line(coverage)
        def build(evidence: Mapping[str, list[tuple[str, str, str, str]]]) -> Briefing:
            return compose(self.now, triaged, evidence_for=evidence, coverage_line=line, coverage_gaps=gaps,
                           unresolved_signals=unresolved, legacy_lines=legacy, goal_lines=sections[0],
                           plan_lines=sections[1], responsibility_lines=sections[2], coordination_lines=sections[3],
                           commitment_count=commitments,
                           subject_labels=self.deps.subject_labels, fmt=fmt, open_items=open_items,
                           calendar_line=cal_line, incumbent_lines=incumbent_lines)
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
        from catchup_core.corpus import record_outcome  # noqa: PLC0415
        observed_cohort = dict(self.deps.evaluation_cohort)
        if observed_cohort and not self.deps.extractor_identity_matched:
            observed_cohort['model'] = 'unverified'
        permission_generation = led._permission_generation()
        provider_signal_generations = {
            sid: generation for sid, generation in self.provider_signal_generations.items()
            if generation == permission_generation and self._provider_signal_current(sid, self.deps.viewer)}
        record_outcome(led, req.catchup_id, {
            "catchup_id": req.catchup_id, "versions": {**self.versions, "provider": self.deps.provider_label},
            "cohort": observed_cohort,
            "permission_generation": permission_generation,
            "provider_signal_generations": provider_signal_generations,
            "signals": [r[0] for r in led.conn.execute("SELECT signal_id FROM signals")],
            "alerts": [{"obligation_key": a.obligation_key,
                        "signals": [sid for sid, _start, _end in led.get_fact(a.fact_id, self.deps.viewer).evidence]}
                       for a in triaged.alerts if a.obligation_key in briefing.surfaced],
            "quiet_claim": briefing.quiet, "coverage_complete": not has_gaps,
            "citations_resolve": not briefing.missing,
        }, self.now)
        def finalize() -> tuple[str, ...]:
            if self.deps.items_publisher is not None and req.mode is ExecutionMode.AUTHORITATIVE:
                self.deps.items_publisher((*triaged.alerts, *triaged.horizon))
            if self.deps.reminder_proposer is not None and req.mode is ExecutionMode.AUTHORITATIVE:
                self.deps.reminder_proposer((*triaged.alerts, *triaged.horizon))
            self.deps.finalizer(req.catchup_id)
            return ()
        self.stage(Stage.FINALIZED, finalize)

        failures = tuple(dict.fromkeys(self.failures))
        status = CatchupResult.derive_status(tuple(coverage), failures)
        if (unresolved or triaged.uncertain or gaps) and status is RunStatus.COMPLETE:
            status = RunStatus.DEGRADED
        for capability in ("RA-01", "RA-04", "RA-08", "RA-16", "RA-17"):
            led.record_capability(capability, req.catchup_id, succeeded=status is RunStatus.COMPLETE)
        if triaged.alerts:
            led.record_capability("RA-03", req.catchup_id)
        if triaged.cross_domain:
            led.record_capability("RA-13", req.catchup_id)
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
        fact_ids = list({a.fact_id for a in triaged.alerts + triaged.horizon + triaged.supporting}
                        | set(self.context_facts))
        versions = {fid: led.get_fact(fid, self.deps.viewer).recorded_at for fid in fact_ids}
        if any(versions[fid] != version for fid, version in self.context_facts.items()):
            raise LedgerError('Goal or commitment evidence changed before composition')
        context_digest = hashlib.sha256(briefing.text.encode()).hexdigest()
        saved = led.draft(req.catchup_id, {"text": briefing.text, "fact_ids": fact_ids,
                                         "fact_versions": versions,
                                         "fact_generation": self.context_generation,
                                         "composition_digest": context_digest,
                                         "context_version": self.context_version,
                                         "permission_generation": led._permission_generation()})
        self.saved_draft = saved
        self._check_publication_inputs()
        if saved and saved.get("permission_generation", 0) != led._permission_generation():
            raise LedgerError("permissions changed since composition; start a new Catch-up")
        if saved and saved.get("composition_digest") != context_digest:
            raise LedgerError("Briefing inputs changed since composition; start a new Catch-up")
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

    def _check_publication_inputs(self) -> None:
        self.deps.validate_inputs()
        saved = self.saved_draft or {}
        if saved.get('permission_generation') != self.led._permission_generation():
            raise LedgerError('permissions changed before publication; start a new Catch-up')
        if saved.get('context_version') != self.deps.context_version():
            raise LedgerError('curated inputs changed before publication; start a new Catch-up')
        if saved.get('fact_generation') != self.led.fact_generation():
            raise LedgerError('evidence set changed before publication; start a new Catch-up')
        versions = saved.get('fact_versions', {})
        if not isinstance(versions, dict):
            raise LedgerError('invalid draft evidence versions')
        for fid, version in versions.items():
            if self.led.get_fact(fid, self.deps.viewer).recorded_at != version:
                raise LedgerError('evidence changed before publication; start a new Catch-up')

    def _commit(self, text: str) -> tuple[str | None, int | None]:
        req = self.request
        if req.mode is not ExecutionMode.AUTHORITATIVE:
            self.stage(Stage.COMMIT, lambda: ())
            return None, None
        published: list[int] = []

        def do_commit() -> tuple[str, ...]:
            self._check_publication_inputs()
            if self.deps.publisher is not None:
                self.deps.publisher(req.catchup_id, text, 1)
            version, _created = self.led.publish_briefing(req.catchup_id, text)
            published.append(version)
            return (f"brf_{req.catchup_id}",)

        self.stage(Stage.COMMIT, do_commit)
        if not published:                            # resumed after COMMIT: read the version back
            published.append(self.led.publish_briefing(req.catchup_id, text)[0])
        return f"brf_{req.catchup_id}", published[0]


def _rebase_span(text: str, chunk_start: int, start: int, end: int, unit: str) -> tuple[int, int]:
    if unit == 'unicode-codepoint':
        chunk = text[chunk_start:].encode('utf-8')
        return (chunk_start + len(chunk[:start].decode('utf-8')),
                chunk_start + len(chunk[:end].decode('utf-8')))
    offset = len(text[:chunk_start].encode('utf-8'))
    return offset + start, offset + end


def fair_backlog_slice(rows: Sequence[sqlite3.Row], limit: int = 500) -> list[sqlite3.Row]:
    """Bound observation backlog fairly across distinct connectors to prevent starvation (Row 5 / RA-10 / RA-16)."""
    if len(rows) <= limit or limit <= 0:
        return list(rows)
    grouped: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        conn = "default"
        if "connector" in r.keys():  # noqa: SIM118 - sqlite3.Row containment checks values
            conn = r["connector"]
        grouped.setdefault(conn, []).append(r)
    selected: list[sqlite3.Row] = []
    iterators = {k: iter(v) for k, v in grouped.items()}
    while len(selected) < limit and iterators:
        exhausted = []
        for k, it in iterators.items():
            try:
                selected.append(next(it))
                if len(selected) >= limit:
                    break
            except StopIteration:
                exhausted.append(k)
        for k in exhausted:
            del iterators[k]
    return selected


def run_catchup(request: CatchupRequest, deps: Deps) -> CatchupResult:
    """Run (or resume) one Catch-up and return its typed result."""
    run: _Run | None = None
    started: float | None = None
    with contextlib.suppress(Exception):
        started = deps.perf()
    try:
        with deps.maintenance(), session_lock(deps.ledger._path.with_suffix(".session.lock")):
            deps.ledger._blob_deleter = deps.blobs.delete
            deps.ledger.resume_pending_forgets()
            deps.ledger.resume_pending_cancellations()
            sequence = deps.ledger.next_metric_sequence(request.catchup_id, implementation_version=deps.input_version)
            run = _Run(request, deps, sequence)
            try:
                result = run.run()
            except AwaitingExtraction:
                _record_metrics(deps, run, request, "paused", started)
                raise
            _record_metrics(deps, run, request, result.status.value, started)
            return result
    except PermissionDenied as exc:
        _record_metrics(deps, run, request, 'blocked', started)
        return CatchupResult(request.catchup_id, request.mode, RunStatus.BLOCKED,
                             reason_unpublished=str(exc), failures=(FailureClass.PERMISSION,))
    except (OSError, LedgerError, SessionBusy, sqlite3.Error, DecryptionError, KeyUnavailableError) as exc:
        _record_metrics(deps, run, request, 'blocked', started)
        return CatchupResult(request.catchup_id, request.mode, RunStatus.BLOCKED,
                             reason_unpublished=str(exc), failures=(FailureClass.STORAGE,))


def _record_metrics(deps: Deps, run: _Run | None, request: CatchupRequest, status: str, started: float | None) -> None:
    """One line per invocation; a failing sink must never change the outcome of a Catch-up."""
    try:
        entry: dict[str, object] = {
            'schema': 'artha.metrics.invocation.v2', 'invocation_id': uuid.uuid4().hex,
            'metric_sequence': run.metric_sequence if run else None,
            "catchup_id": request.catchup_id, "mode": request.mode.value, "status": status,
            'implementation_version': deps.input_version, 'pack_version': deps.pack.version,
            'policy_version': request.policy_version,
            "at": _iso(run.now if run else deps.now()), 'ended_at': _iso(deps.now()),
            "total_seconds": round(deps.perf() - started, 3) if started is not None else 0.0,
            'measurement_complete': run is not None and started is not None,
            "stages": dict(run.timings) if run else {},
            "model_requests": run.model_requests if run else 0,
            "prompt_chars": run.prompt_chars if run else 0, "answer_chars": run.answer_chars if run else 0,
            'extraction_rejections': dict(run.extraction_rejections) if run else {},
            "resumed": bool(run.done - set(run.timings)) if run else True,
        }
    except Exception:
        return
    for sink in (deps.metrics, deps.trace):                  # independent: one failing sink never skips the other
        with contextlib.suppress(Exception):
            sink(entry)


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


def link_predicate(pack: DomainPack, predicate: str) -> str:
    """The predicate an obligation key is built from: a settlement shares the key of what it settles."""
    return pack.predicates[predicate].resolves or predicate


def _obligation_origin(reference: str, text: str, upstream: str) -> str:
    """Cross-message links require a grounded name, never a date or an invented model ID.

    Equal names can still disagree: reconciliation disputes conflicting independent sources.
    Without a grounded name we preserve separate evidence rather than guess a merge.
    """
    tokens = re.findall(r"[a-z0-9]+", reference.casefold())
    words = set(re.findall(r"[a-z0-9]+", text.casefold()))
    if tokens and all(t in words for t in tokens) and any(not t.isdigit() for t in tokens):
        return "named:" + "-".join(tokens)
    return "source:" + upstream + "|" + reference


def _typed(kind: str, raw: object) -> object:
    from datetime import date  # noqa: PLC0415
    if kind == "date":
        return date.fromisoformat(str(raw))
    if kind == "datetime":
        return datetime.fromisoformat(str(raw))
    return raw
