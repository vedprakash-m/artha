from __future__ import annotations

import json
import sqlite3
from argparse import Namespace
from dataclasses import replace

import pytest
from test_coordinator import CIPHER, KEY, NOW, PACK, model_for, req

from catchup_core import coordinator as co
from catchup_core import documents, field_confirmation
from catchup_core.actions import ActionError, ActionStore, ClaimRefused, verify_evidence_preconditions
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import ExecutionMode, FactStatus, ProcessingState
from catchup_core.fact_access import AccessPolicy, require_metadata
from catchup_core.ledger import FactInput, Ledger, LedgerError, Provenance


@pytest.fixture
def runtime(tmp_path):
    with Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="ved",
                clock=NOW.isoformat) as led:
        led.migrate()
        predicate = "exam_registration_deadline"
        pack = replace(PACK, predicates={**PACK.predicates, predicate: replace(
            PACK.predicates[predicate], authority=("documents",))})
        deps = co.Deps(led, pack, EncryptedBlobStore(tmp_path / "blobs", CIPHER, KEY), KEY,
                       {}, lambda _: "", ["kid1"], "ved", lambda: NOW,
                       source_class={"documents": "documents"})
        yield led, deps


def ingest_candidate(led, deps, *, method="ocr", value="2026-10-14", text=None):
    text = text or f"Subject: kid1\nPage 1\nRegistration closes {value}"
    record = co.ConnectorRecord("doc_synthetic:p1", text, document_method=method)
    execution = co._Run(req("cu_document"), deps, 0)
    execution._store_fetch(co.ConnectorFetch("documents", "manual", (record,)), "documents")
    sid = led.conn.execute("SELECT signal_id FROM source_heads WHERE connector='documents'").fetchone()[0]
    start = text.encode().index(value.encode())
    led.put_candidates(sid, [{"predicate": "exam_registration_deadline", "value": value,
        "subject_id": "kid1", "span": [start, start + len(value.encode())], "quote": value}])
    led.set_processing_state(sid, ProcessingState.EXTRACTED)
    execution._stage_reconcile()
    fid = led.conn.execute("SELECT fact_id FROM fact_evidence WHERE signal_id=?", (sid,)).fetchone()[0]
    return fid, sid


def preconditions(led, fid):
    return {"core_derived": True, "fact_versions": {fid: led.get_fact(fid, "ved").recorded_at},
            "subjects": ["kid1"], "permission_generation": led._permission_generation(),
            "fresh_until": "2026-10-13T00:00:00Z"}


@pytest.mark.parametrize("deadline,accepted", [
    pytest.param("October 14", False, id="yearless"),
    pytest.param("2026-10-14", True, id="explicit"),
])
@pytest.mark.parametrize("method", ["text", "ocr"])
def test_ingested_document_deadline_cannot_borrow_upload_year(runtime, tmp_path, monkeypatch, deadline, accepted,
                                                           method):
    led, deps = runtime
    page = f"Registration closes {deadline}"
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"%PDF-synthetic document")
    monkeypatch.setattr(documents, "extract_pdf", lambda _: {
        "pages": [page], "complete": True, "page_methods": [method],
    })
    identity = documents.ingest(source, "kid1", led, "ved", NOW)
    encrypted = (led._path.parent / "documents" / f"{identity}.ae1").read_bytes()
    assert page.encode() not in encrypted
    payload = json.loads(CIPHER.decrypt(encrypted, aad=identity.encode()))
    assert payload["observed_at"] == NOW.isoformat()

    fetched = documents.fetch_documents(led, "ved")
    assert len(fetched.records) == 1
    record = fetched.records[0]
    assert record.upstream_id == f"{identity}:p1"
    assert record.document_method == method
    assert not record.incomplete
    deps.extractor = model_for({deadline: ("exam_registration_deadline", "2026-10-14", "kid1")})
    execution = co._Run(req("cu_document_source_time"), deps, 0)
    execution._store_fetch(fetched, "documents")
    execution._stage_normalize()
    execution._stage_extract()
    execution._stage_reconcile()

    observed_at = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    facts = led.facts_matching("kids_school", "exam_registration_deadline", "kid1",
                              statuses=(FactStatus.CURRENT, FactStatus.DISPUTED, FactStatus.UNKNOWN))
    assert bool(facts) is accepted
    if accepted:
        assert len(facts) == 1
        assert str(facts[0].value) == "2026-10-14"
        assert facts[0].status == ("current" if method == "text" else "unknown")
        assert facts[0].provenance_class == ("verified_source" if method == "text" else "model_hypothesis")
        stored = led.conn.execute("SELECT observed_at,occurred_at FROM facts").fetchone()
        assert stored["observed_at"] == observed_at
        assert stored["occurred_at"] is None
    else:
        assert led.failed_signals()
    signal = led.conn.execute("SELECT observed_at,occurred_at FROM signals WHERE connector='documents'").fetchone()
    assert signal["observed_at"] == observed_at
    assert signal["occurred_at"] is None
    assert record.occurred_at is None


def test_high_confidence_ocr_requires_review_before_action_metadata(runtime):
    led, deps = runtime
    pages, metadata = documents.merge_ocr([""], {0: Namespace(text="2026-10-14", confidence=99.9)})
    assert metadata["complete"]
    fid, _ = ingest_candidate(led, deps, text="Subject: kid1\nPage 1\n" + pages[0])
    assert led.get_fact(fid, "ved").status == "unknown"
    assert led.get_fact_metadata(fid, "ved").status == "unknown"
    assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(preconditions(led, fid), ledger=led, viewer="ved", now=NOW.isoformat())


def test_exact_confirmation_is_reusable_without_action_approval(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    version = led.get_fact(fid, "ved").recorded_at
    applicability = led.get_fact_metadata(fid, "ved")
    assert applicability.validity_unknown
    preview = field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)
    token = preview["apply_token"]
    receipt = field_confirmation.field_command(Namespace(fact_id=fid, apply=token, reopen=False), deps)
    assert receipt == field_confirmation.field_command(Namespace(fact_id=fid, apply=token, reopen=False), deps)
    assert led.get_fact(fid, "ved").recorded_at == version
    confirmed = led.get_fact_metadata(fid, "ved")
    assert confirmed.status == "current"
    assert confirmed.provenance_class == "verified_source"
    assert (confirmed.valid_from, confirmed.valid_to, confirmed.validity_unknown) == (
        applicability.valid_from, applicability.valid_to, applicability.validity_unknown)
    with pytest.raises(ClaimRefused, match="action evidence validity is unknown"):
        verify_evidence_preconditions(preconditions(led, fid), ledger=led, viewer="ved", now=NOW.isoformat())
    replay, _ = ingest_candidate(led, deps)
    assert replay == fid
    assert led.get_fact(fid, "ved").status == "current"
    assert led.get_fact_metadata(fid, "ved").validity_unknown
    with pytest.raises(ClaimRefused, match="action evidence validity is unknown"):
        verify_evidence_preconditions(preconditions(led, fid), ledger=led, viewer="ved", now=NOW.isoformat())
    assert led.conn.execute("SELECT COUNT(*) FROM document_field_confirmations").fetchone()[0] == 1
    assert not led.conn.execute("SELECT 1 FROM sqlite_master WHERE name='action_proposals'").fetchone()
    actions = sqlite3.connect(":memory:")
    try:
        store = ActionStore(actions, clock=NOW.isoformat)
        store.propose("synthetic", operation_key="synthetic", payload={"title": "synthetic"},
                      expires_at="2026-10-13T00:00:00Z")
        with pytest.raises(ActionError):
            store.claim("synthetic", ledger=led, viewer="ved")
    finally:
        actions.close()


def confirm(led, deps, fid):
    preview = field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)
    return field_confirmation.field_command(Namespace(fact_id=fid, apply=preview["apply_token"], reopen=False), deps)


@pytest.mark.parametrize("mutation", ["withdraw", "source", "forget", "consent", "domain_consent", "revoked",
    "value", "receipt", "link", "scope", "subjects", "missing_receipt", "incomplete"])
def test_invalidated_field_blocks_reads_and_keyless_action_metadata_immediately(runtime, mutation):
    led, deps = runtime
    fid, sid = ingest_candidate(led, deps)
    led._consent_subjects = frozenset({"kid1"})
    for scope in ("kids_school", "documents:manual"):
        led.grant_consent("kid1", "kid1", scope, ["view", "correct", "export"], "ved")
    assertion = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "independent",
        "kid1", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    confirm(led, deps, fid)
    pre = preconditions(led, fid)
    if mutation == "withdraw":
        preview = field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=True), deps)
        field_confirmation.field_command(Namespace(fact_id=fid, apply=preview["apply_token"], reopen=True), deps)
    elif mutation == "source":
        row = led.signal_row(sid)
        newer, _ = led.upsert_signal(co.SignalInput("documents", "manual", row["upstream_id"], "changed",
            NOW.isoformat(), "changed", "changed"))
        led.observe_source_versions([newer], complete=True)
    elif mutation == "forget":
        led.forget("documents", "manual", "doc_synthetic:p1")
    elif mutation == "consent":
        led.conn.execute("UPDATE consent SET operations='[\"view\",\"export\"]' WHERE scope='documents:manual'")
    elif mutation == "domain_consent":
        led.conn.execute("UPDATE consent SET operations='[\"view\",\"export\"]' WHERE scope='kids_school'")
    elif mutation == "revoked":
        grant = led.conn.execute("SELECT consent_id FROM consent WHERE scope='documents:manual'").fetchone()[0]
        led.revoke_consent(grant)
    elif mutation == "value":
        encrypted, _ = CIPHER.encrypt_json("forged", aad=fid.encode())
        led.conn.execute("UPDATE facts SET typed_value=? WHERE fact_id=?", (encrypted, fid))
    elif mutation == "receipt":
        led.conn.execute("UPDATE document_field_confirmations SET payload='forged'")
    elif mutation == "link":
        led.conn.execute("UPDATE document_field_links SET span_end=span_end+1")
    elif mutation == "scope":
        led.conn.execute("UPDATE document_field_links SET scope='[\"kid1\",\"other\"]'")
    elif mutation == "subjects":
        led.conn.execute("UPDATE signal_access SET subjects='[]'")
    elif mutation == "missing_receipt":
        led.conn.execute("DELETE FROM document_field_confirmations")
    else:
        led.conn.execute("UPDATE source_heads SET complete=0")
    if mutation in ("forget", "revoked"):
        with pytest.raises(LedgerError):
            led.get_fact(fid, "ved")
    else:
        assert led.get_fact(fid, "ved").status == "unknown"
    assert led.get_fact(assertion, "ved").provenance_class == "user_confirmed"
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(pre, ledger=led, viewer="ved", now=NOW.isoformat())
    led._cipher = None
    if mutation not in ("forget", "revoked"):
        assert led.get_fact_metadata(fid, "ved").status == "unknown"
        assert led.get_fact_metadata(fid, "ved").provenance_class == "model_hypothesis"


def test_current_receipt_authorizes_query_only_worker_without_keys(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    with sqlite3.connect(led._path) as worker:
        worker.row_factory = sqlite3.Row
        worker.execute("PRAGMA query_only=ON")
        policy = AccessPolicy("ved", frozenset(), None)
        assert require_metadata(worker, policy, fid, "ved", NOW.isoformat()).status == "current"
        before = worker.total_changes
        assert require_metadata(worker, policy, fid, "ved", NOW.isoformat()).provenance_class == "verified_source"
        assert worker.total_changes == before


def test_confirmation_does_not_authorize_another_fact_with_identical_value_and_span(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    fact = led.get_fact(fid, "ved")
    other = led.put_fact(FactInput(fact.domain, fact.predicate, fact.value, fact.primary_subject_id,
        NOW.isoformat(), status=FactStatus.UNKNOWN,
        provenance=Provenance.MODEL_HYPOTHESIS, evidence=fact.evidence, obligation_key="independent-field"))
    confirm(led, deps, fid)
    assert led.get_fact_metadata(fid, "ved").status == "current"
    assert led.get_fact(other, "ved").status == "unknown"
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(preconditions(led, other), ledger=led, viewer="ved", now=NOW.isoformat())


@pytest.mark.parametrize("method", [None, "unknown", "ocr"])
def test_untrusted_same_upstream_never_promotes_or_disputes_verified_fact(runtime, method):
    led, deps = runtime
    old, _ = ingest_candidate(led, deps, method="text")
    assert led.get_fact(old, "ved").provenance_class == "verified_source"
    newer, _ = ingest_candidate(led, deps, method=method, value="2026-10-15")
    assert led.get_fact(newer, "ved").status == "unknown"
    assert led.get_fact(old, "ved").status not in ("disputed", "superseded")


def test_replaced_source_can_be_reviewed_and_independent_correction_survives(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    same, _ = ingest_candidate(led, deps, text="Subject: kid1\nPage 1\nUpdated layout 2026-10-14")
    assert same == fid
    assert led.get_fact(fid, "ved").status == "unknown"
    confirm(led, deps, fid)
    assert led.get_fact(fid, "ved").status == "current"
    corrected = FactInput("kids_school", "exam_registration_deadline", "2026-10-16", "kid1",
                          NOW.isoformat(), provenance=Provenance.USER_CONFIRMED)
    led.put_fact(corrected, fact_id=fid)
    led.forget("documents", "manual", "doc_synthetic:p1")
    assert led.get_fact(fid, "ved").value == "2026-10-16"
    assert led.get_fact_metadata(fid, "ved").provenance_class == "user_confirmed"


def test_whole_catchup_surfaces_unreviewed_ocr_then_reuses_confirmed_field(runtime):
    led, deps = runtime
    text = "Subject: kid1\nPage 1\nRegistration closes 2026-10-14"
    record = co.ConnectorRecord("doc_synthetic:p1", text, document_method="ocr")
    deps.fetchers = {"documents": lambda: co.ConnectorFetch("documents", "manual", (record,))}
    deps.extractor = model_for({"2026-10-14": ("exam_registration_deadline", "2026-10-14", "kid1")})
    result = co.run_catchup(req("cu_full_document"), deps)
    assert result.briefing_text
    fid = led.conn.execute("SELECT fact_id FROM facts").fetchone()[0]
    assert led.get_fact(fid, "ved").status == "unknown"
    assert "unverified" in result.briefing_text.lower() or "unknown" in result.briefing_text.lower()
    confirm(led, deps, fid)
    version = led.get_fact(fid, "ved").recorded_at
    replay = co.run_catchup(req("cu_full_document_replay"), deps)
    assert replay.briefing_text
    assert led.get_fact(fid, "ved").status == "current"
    assert led.get_fact(fid, "ved").recorded_at == version
    assert led.conn.execute("SELECT COUNT(*) FROM document_field_confirmations").fetchone()[0] == 1


def test_failed_link_publication_rolls_back_receipt_and_projection(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    preview = field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)
    led.conn.execute("CREATE TRIGGER fail_link BEFORE INSERT ON document_field_links "
                     "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        field_confirmation.field_command(Namespace(fact_id=fid, apply=preview["apply_token"], reopen=False), deps)
    assert led.get_fact(fid, "ved").status == "unknown"
    assert not led.conn.execute("SELECT 1 FROM document_field_confirmations").fetchone()
    led.conn.execute("DROP TRIGGER fail_link")
    field_confirmation.field_command(Namespace(fact_id=fid, apply=preview["apply_token"], reopen=False), deps)
    assert led.get_fact(fid, "ved").status == "current"


def test_configured_source_class_cannot_launder_ocr_authority(runtime, monkeypatch):
    led, deps = runtime
    monkeypatch.setattr(co._Run, "_source_class", lambda *_: "documents")
    fid, _ = ingest_candidate(led, deps)
    assert led.conn.execute("SELECT status FROM facts WHERE fact_id=?", (fid,)).fetchone()[0] == "unknown"


@pytest.mark.parametrize("mutation", ["viewer", "shadow", "non_document", "page", "no_head"])
def test_command_refuses_non_owner_shadow_and_unresolved_document_bindings(runtime, mutation):
    led, deps = runtime
    fid, sid = ingest_candidate(led, deps)
    if mutation == "viewer":
        deps.viewer = "kid1"
    elif mutation == "shadow":
        deps.mode = ExecutionMode.SHADOW
    elif mutation == "non_document":
        led.conn.execute("UPDATE signals SET connector='school_email' WHERE signal_id=?", (sid,))
    elif mutation == "page":
        led.conn.execute("UPDATE signals SET upstream_id='unresolved' WHERE signal_id=?", (sid,))
    else:
        led.conn.execute("UPDATE source_heads SET signal_id=NULL")
    with pytest.raises(LedgerError):
        field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)
    assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()


def test_keyless_guard_fails_closed_without_field_link_schema(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    led.conn.execute("DROP TABLE document_field_links")
    led._cipher = None
    assert led.get_fact_metadata(fid, "ved").status == "unknown"


def test_unrelated_permission_generation_does_not_erase_runtime_receipt(runtime):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    grant = led.grant_consent("other", "other", "health", ["view"], "ved")
    led.revoke_consent(grant)
    assert led.get_fact(fid, "ved").status == "current"
    assert led.get_fact_metadata(fid, "ved").status == "current"


@pytest.mark.parametrize("method", ["confidence 99%", True, 1])
def test_connector_record_rejects_nonliteral_document_methods(method):
    with pytest.raises(ValueError):
        co.ConnectorRecord("synthetic", "synthetic", document_method=method)