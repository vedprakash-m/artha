from __future__ import annotations

import sqlite3
from argparse import Namespace
from dataclasses import replace

import pytest
from core_helpers import (
    COORDINATOR_CIPHER as CIPHER,
    CORE_KEY as KEY,
    CORE_NOW as NOW,
    CORE_PACK as PACK,
    FakeKeyring,
    model_for,
    coord_req as req,
    write_cfg,
)

from catchup_core import coordinator as co, curated, fact_access, runtime as rt
from catchup_core import documents, field_confirmation
from catchup_core.actions import ActionError, ActionStore, ClaimRefused, verify_evidence_preconditions
from catchup_core.blobs import EncryptedBlobStore
from catchup_core.contracts import CoverageStatus, ExecutionMode, FactStatus, FailureClass, ProcessingState
from catchup_core.fact_access import AccessPolicy, require_metadata
from catchup_core.ledger import FactInput, Ledger, LedgerError, PermissionDenied, Provenance, SignalInput
from catchup_core.runtime import build_fetchers


@pytest.fixture
def runtime(tmp_path):
    with Ledger(tmp_path / "ledger.db", id_key=KEY, cipher=CIPHER, owner_id="ved",
                clock=NOW.isoformat) as led:
        led.migrate()
        upsert_signal = led.upsert_signal

        def upsert_reviewed_signal(signal):
            signal_id, created = upsert_signal(signal)
            row = led.signal_row(signal_id)
            if not led._has_personal_binding(
                    "signal", signal_id, row["upstream_version"], row["connector"],
                    row["account_scope"], row["content_hmac"]):
                with led.transaction() as db:
                    led._record_personal_binding(
                        db, "signal", signal_id, row["upstream_version"], row["connector"],
                        row["account_scope"], row["content_hmac"], provenance="source_review")
            return signal_id, created

        led.upsert_signal = upsert_reviewed_signal
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


def test_provider_fetchers_stay_unavailable_without_trusted_classification():
    config = type("Config", (), {"sources": {"school_email": {"account_scope": "synthetic-account"}}})()

    def forbidden(*_args):
        pytest.fail("unclassified source reached provider setup")

    fetchers = build_fetchers(config, "2026-10-01T00:00:00Z", forbidden, forbidden,
                              {"school_email": True})
    result = fetchers["school_email"]()

    assert result.status is CoverageStatus.UNAVAILABLE
    assert result.error is FailureClass.PERMISSION
    assert result.records == () and result.blind_spots == ("personal source classification unavailable",)


def test_capture_fetcher_stays_unavailable_without_trusted_classification(runtime):
    ledger, deps = runtime

    result = rt._capture_fetcher(ledger._path.parent, CIPHER, deps.viewer)()

    assert result.status is CoverageStatus.UNAVAILABLE
    assert result.error is FailureClass.PERMISSION
    assert result.records == () and result.blind_spots == ("personal source classification unavailable",)


def test_runtime_blocks_legacy_state_and_fingerprints_unavailable_curated_inputs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    state = repo / "state"
    state.mkdir(parents=True)
    (state / "finance.md").write_text("# Synthetic unknown legacy data\n")
    goals = state / "goals.md"
    goals.write_text("goals:\n- id: G\n  owner: ved\n  subject: kid1\n  title: A\n")
    write_cfg(repo)
    deps = rt.build_deps(req("cu_privacy_runtime", mode=ExecutionMode.SHADOW), repo_root=repo,
        environ={"ARTHA_LOCAL_DIR": str(tmp_path / "private")}, keyring_backend=FakeKeyring(),
        handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        reads = []
        original_read = curated.read_state

        def tracked_read(directory, name):
            reads.append(name)
            return original_read(directory, name)

        monkeypatch.setattr(curated, "read_state", tracked_read)
        denied_version = deps.context_version()
        assert reads == []

        goals.write_text("goals:\n- id: G\n  owner: ved\n  subject: kid1\n  title: Longer synthetic title\n")
        changed_version = deps.context_version()
        assert reads == []
        assert changed_version == denied_version

        monkeypatch.setattr(deps.ledger, "signals_in_state",
                            lambda *_args, **_kwargs: pytest.fail("legacy Signals entered personal output"))
        execution = co._Run(req("cu_privacy_runtime"), deps, 0)
        output = execution._incumbent_output()
        assert not output.conclusions
        assert any(entry.status is CoverageStatus.UNAVAILABLE for entry in output.coverage)

        deps.finalizer("cu_privacy_runtime")
        assert not (tmp_path / "private" / "catchup_core" / "shadow" / "snapshots").exists()
    finally:
        deps.ledger.close()


def test_context_fingerprint_refuses_permission_change_during_unavailable_admission(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    state = repo / "state"
    state.mkdir(parents=True)
    goals = state / "goals.md"
    goals.write_text("goals:\n- id: G\n  owner: ved\n  title: Synthetic goal\n")
    write_cfg(repo)
    deps = rt.build_deps(req("cu_permission_fingerprint", mode=ExecutionMode.SHADOW), repo_root=repo,
        environ={"ARTHA_LOCAL_DIR": str(tmp_path / "private")}, keyring_backend=FakeKeyring(),
        handler_loader=lambda _: None, auth_loader=lambda _: {})
    try:
        consent_id = deps.ledger.grant_consent(
            "adult1", "adult1", "curated:goals", ("view",), deps.viewer)
        original_identity = curated._personal_state_identity
        revoked = False

        def change_permission(directory, name, ledger):
            nonlocal revoked
            if not revoked:
                deps.ledger.revoke_consent(consent_id)
                revoked = True
            return original_identity(directory, name, ledger)

        monkeypatch.setattr(curated, "_personal_state_identity", change_permission)
        monkeypatch.setattr(curated, "read_state",
                            lambda *_args, **_kwargs: pytest.fail("unavailable State payload was read"))
        with pytest.raises(PermissionDenied, match="permissions changed"):
            deps.context_version()
    finally:
        deps.ledger.close()


@pytest.mark.parametrize("deadline,accepted", [
    pytest.param("October 14", False, id="yearless"),
    pytest.param("2026-10-14", True, id="explicit"),
])
@pytest.mark.parametrize("method", ["text", "ocr"])
def test_reviewed_document_record_deadline_cannot_borrow_upload_year(
        runtime, admitted_documentary_scope, deadline, accepted, method):
    led, deps = runtime
    page = f"Registration closes {deadline}"
    record = co.ConnectorRecord("doc_synthetic:p1", f"Subject: kid1\nPage 1\n{page}",
                                document_method=method)
    execution = co._Run(req("cu_document_source_time"), deps, 0)
    execution._store_fetch(co.ConnectorFetch("documents", "manual", (record,), required=False), "documents")
    execution._stage_normalize()
    deps.extractor = model_for({deadline: ("exam_registration_deadline", "2026-10-14", "kid1")})
    execution._stage_extract()
    execution._stage_reconcile()
    sid = led.conn.execute("SELECT signal_id FROM source_heads WHERE connector='documents'").fetchone()[0]

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
    signal = led.conn.execute("SELECT observed_at,occurred_at FROM signals WHERE signal_id=?", (sid,)).fetchone()
    assert signal["observed_at"] == observed_at
    assert signal["occurred_at"] is None
    assert record.occurred_at is None


def test_high_confidence_ocr_requires_review_before_action_metadata(runtime, admitted_documentary_scope):
    led, deps = runtime
    pages, metadata = documents.merge_ocr([""], {0: Namespace(text="2026-10-14", confidence=99.9)})
    assert metadata["complete"]
    fid, _ = ingest_candidate(led, deps, text="Subject: kid1\nPage 1\n" + pages[0])
    assert led.get_fact(fid, "ved").status == "unknown"
    assert led.get_fact_metadata(fid, "ved").status == "unknown"
    assert not led.conn.execute("SELECT 1 FROM document_field_requirements").fetchone()
    with pytest.raises(ClaimRefused):
        verify_evidence_preconditions(preconditions(led, fid), ledger=led, viewer="ved", now=NOW.isoformat())


def test_exact_confirmation_is_reusable_without_action_approval(runtime, admitted_documentary_scope):
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


@pytest.mark.parametrize("surface", ["metadata", "emergency"])
def test_unbound_document_fact_is_denied_before_its_payload_is_selected(
        runtime, admitted_documentary_scope, surface):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    assert led.get_fact_metadata(fid, "ved").status == "current"
    led.conn.execute(
        "DELETE FROM scope_bindings WHERE object_type='fact' AND object_id=?", (fid,))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "facts" and column == "typed_value":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        if surface == "metadata":
            with pytest.raises(PermissionDenied, match="scope"):
                led.get_fact_metadata(fid, "ved")
        else:
            from catchup_core.interchange import emergency_view
            assert emergency_view(led, "ved")["facts"] == []
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


def test_unbound_document_history_is_denied_before_any_fact_payload_is_selected(
        runtime, admitted_documentary_scope):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    led.conn.execute(
        "DELETE FROM scope_bindings WHERE object_type='fact_version' AND object_id=?", (fid,))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table in ("facts", "fact_versions") and column == "typed_value":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        with pytest.raises(PermissionDenied, match="scope"):
            led.fact_history(fid, "ved")
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


def confirm(led, deps, fid):
    preview = field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)
    return field_confirmation.field_command(Namespace(fact_id=fid, apply=preview["apply_token"], reopen=False), deps)


@pytest.mark.parametrize("mutation", ["withdraw", "source", "forget", "consent", "domain_consent", "revoked",
    "value", "receipt", "link", "scope", "subjects", "missing_receipt", "incomplete"])
def test_invalidated_field_blocks_reads_and_keyless_action_metadata_immediately(
        runtime, admitted_documentary_scope, mutation):
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
    if mutation in ("forget", "revoked", "value"):
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


@pytest.mark.parametrize("bound", [True, False])
@pytest.mark.parametrize("entrypoint", ["helper", "worker"])
def test_query_only_worker_refuses_document_fact_without_scope_authority(
        runtime, admitted_documentary_scope, bound, entrypoint):
    from lib.action_evidence import _MetadataLedger

    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    if not bound:
        led.conn.execute(
            "DELETE FROM scope_bindings WHERE object_type='fact' AND object_id=?", (fid,))
    reads = []
    with sqlite3.connect(led._path) as worker:
        worker.row_factory = sqlite3.Row
        worker.execute("PRAGMA query_only=ON")
        policy = AccessPolicy("ved", frozenset(), None)

        def authorize(action, table, column, _database, _trigger):
            if action == sqlite3.SQLITE_READ and (
                    (table == "facts" and column == "typed_value")
                    or (table in ("document_field_requirements", "document_field_confirmations")
                        and column == "payload")):
                reads.append((table, column))
            return sqlite3.SQLITE_OK

        worker.set_authorizer(authorize)
        try:
            def _attempt():
                if entrypoint == "helper":
                    require_metadata(worker, policy, fid, "ved", NOW.isoformat())
                else:
                    context = {"owner": "ved", "consent_subjects": [], "known_subjects": ["ved", "kid1"]}
                    _MetadataLedger(worker, context, NOW.isoformat()).get_fact_metadata(fid, "ved")

            with pytest.raises(PermissionDenied, match="scope"):
                _attempt()
        finally:
            worker.set_authorizer(None)
        assert worker.total_changes == 0
    assert reads == []
    if bound:
        assert led.get_fact_metadata(fid, "ved").status == "current"


def test_metadata_cannot_borrow_a_more_permissive_caller_policy(runtime):
    led, _ = runtime
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
        "kid1", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    led._consent_subjects = frozenset({"kid1"})

    with pytest.raises(PermissionDenied):
        require_metadata(led.conn, AccessPolicy("ved", frozenset(), None), fid, "ved",
                         NOW.isoformat(), authority=led)


def test_metadata_cannot_use_an_expired_consent_with_a_caller_clock(runtime):
    led, _ = runtime
    led._consent_subjects = frozenset({"kid1"})
    led.grant_consent("kid1", "kid1", "kids_school", ["view"], "ved",
                      expires_at="2026-10-12T15:00:00+00:00")
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
        "kid1", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    policy = AccessPolicy("ved", led._consent_subjects, None)

    with pytest.raises(PermissionDenied):
        require_metadata(led.conn, policy, fid, "ved", "2026-10-12T14:00:00+00:00", authority=led)


@pytest.mark.parametrize("operation", ["export", "emergency"])
def test_metadata_cannot_suppress_source_consent_for_an_operation(
        runtime, admitted_personal_scope, operation):
    led, _ = runtime
    led._consent_subjects = frozenset({"kid1"})
    led.grant_consent("kid1", "kid1", "kids_school", ["view", operation], "ved")
    led.grant_consent("kid1", "kid1", "school_email:personal", ["view"], "ved")
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
        "kid1", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED, evidence=((sid, 0, 1),)))
    policy = AccessPolicy("ved", led._consent_subjects, None)
    assert led.get_fact_metadata(fid, "ved").fact_id == fid

    with pytest.raises(PermissionDenied):
        require_metadata(led.conn, policy, fid, "ved", NOW.isoformat(), operation=operation,
                         source_operation=False, authority=led)


@pytest.mark.parametrize("surface", ["metadata", "fact", "history"])
def test_fact_reads_hold_unverified_authorization_metadata_before_payload(runtime, surface):
    led, _ = runtime
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
        "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if (action == sqlite3.SQLITE_READ and table in ("facts", "fact_versions")
                and column in ("typed_value", "primary_subject_id", "domain", "predicate")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        def _attempt():
            if surface == "metadata":
                led.get_fact_metadata(fid, "ved")
            elif surface == "fact":
                led.get_fact(fid, "ved")
            else:
                led.fact_history(fid, "ved")

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("surface", ["facts_for", "beliefs", "interchange", "emergency"])
def test_fact_lists_report_unavailable_instead_of_an_empty_success(runtime, surface):
    from catchup_core import interchange, views

    led, _ = runtime
    # Per-object scope review: a list holding an unreviewed Fact is unavailable, never an empty success.
    led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
                           "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    def _attempt():
        if surface == "facts_for":
            led.facts_for("kids_school", "exam_registration_deadline", "ved")
        elif surface == "beliefs":
            views.beliefs(led, "ved")
        elif surface == "interchange":
            interchange.build_interchange(led, "ved")
        else:
            interchange.emergency_view(led, "ved")

    with pytest.raises(PermissionDenied, match="scope"):
        _attempt()


@pytest.mark.parametrize("surface", ["matching", "completion", "evaluation"])
def test_empty_fact_dependent_results_do_not_conceal_scope_hold(runtime, surface):
    from catchup_core import heldout

    led, _ = runtime
    # Per-object scope review: an unreviewed match is held, so the result is unavailable rather than empty.
    led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic", "ved", NOW.isoformat(),
                           provenance=Provenance.USER_CONFIRMED, obligation_key="synthetic"))
    led.put_fact(FactInput("kids_school", "commitment_completed", {"record_key": "synthetic", "completed": True},
                           "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED, obligation_key="synthetic"))
    def _attempt():
        if surface == "matching":
            led.facts_matching("kids_school", "exam_registration_deadline", "ved")
        elif surface == "completion":
            curated.completion_fact(led, "ved", None, "synthetic")
        else:
            heldout._facts(led, "synthetic-missing-signal", "ved")

    with pytest.raises(PermissionDenied, match="scope"):
        _attempt()


@pytest.mark.parametrize("operation", ["add_evidence", "set_status", "update"])
def test_fact_mutations_hold_before_existing_payload_or_changes(runtime, operation):
    led, _ = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    fact = FactInput("kids_school", "exam_registration_deadline", "synthetic",
                     "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED)
    fid = led.put_fact(fact)
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if (action == sqlite3.SQLITE_READ and table in ("facts", "fact_versions")
                and column in ("typed_value", "primary_subject_id", "domain", "predicate")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    changes_before = led.conn.total_changes
    try:
        def _attempt():
            if operation == "add_evidence":
                led.add_evidence(fid, sid, 0, 1)
            elif operation == "set_status":
                led.set_fact_status(fid, FactStatus.UNKNOWN)
            else:
                led.put_fact(fact, fact_id=fid)

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert led.conn.total_changes == changes_before
    assert payload_reads == []


def test_retention_inventory_holds_before_unclassified_fact_payload(runtime):
    from catchup_core.retention import RetentionPlanner, RetentionPolicy

    led, _ = runtime
    led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
                          "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table in ("facts", "fact_versions") and column == "typed_value":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    planner = RetentionPlanner(led, RetentionPolicy.unset(), "ved", NOW, led._path.parent)
    led.conn.set_authorizer(authorize)
    try:
        with pytest.raises(PermissionDenied, match="scope"):
            planner._tables()
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


def test_calendar_cancellation_holds_before_journal_or_fact_changes(runtime):
    led, _ = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", "synthetic-event", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
                          "ved", NOW.isoformat(), evidence=((sid, 0, 1),)))
    changes_before = led.conn.total_changes

    with pytest.raises(PermissionDenied, match="scope"):
        led.cancel_calendar("calendar", "personal", "synthetic-event")

    assert led.conn.total_changes == changes_before
    assert not led.conn.execute("SELECT 1 FROM calendar_cancellations").fetchone()


@pytest.mark.parametrize("kind", ["cancelled", "deleted_collections"])
def test_fetch_with_calendar_withdrawals_holds_before_any_local_change(runtime, kind):
    led, deps = runtime
    fetch = co.ConnectorFetch("calendar", "personal", (co.ConnectorRecord("event", "synthetic"),),
                              **{kind: ("synthetic",)})
    run = co._Run(req("cu_synthetic"), deps, 0)
    changes_before = led.conn.total_changes

    with pytest.raises(PermissionDenied, match="scope"):
        run._store_fetch(fetch, "calendar")

    assert led.conn.total_changes == changes_before
    assert not led.conn.execute("SELECT 1 FROM signals").fetchone()


def test_complete_calendar_listing_holds_before_database_or_blob_changes(runtime):
    from catchup_core.triage import CalendarEvent

    led, deps = runtime
    upstream = 'calendar:["synthetic-collection","old-event"]'
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
    fetch = co.ConnectorFetch(
        "calendar", "personal", (co.ConnectorRecord("event", "synthetic"),),
        full_listings=(("synthetic-collection", None, None),))
    run = co._Run(req("cu_synthetic"), deps, 0)
    changes_before = led.conn.total_changes
    assert not deps.blobs._root.exists()

    with pytest.raises(PermissionDenied, match="scope"):
        run._store_fetch(fetch, "calendar")

    assert led.conn.total_changes == changes_before
    assert led.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    assert led.conn.execute("SELECT COUNT(*) FROM calendar_records").fetchone()[0] == 1
    assert not deps.blobs._root.exists()


@pytest.mark.parametrize("status,cursor_reset", [
    (CoverageStatus.DEGRADED, False),
    (CoverageStatus.COMPLETE, True),
])
def test_incomplete_or_reset_calendar_listing_does_not_invoke_withdrawal_hold(
        runtime, status, cursor_reset):
    from catchup_core.triage import CalendarEvent

    led, deps = runtime
    upstream = 'calendar:["synthetic-collection","old-event"]'
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
    fetch = co.ConnectorFetch(
        "calendar", "personal", status=status, cursor_reset=cursor_reset,
        full_listings=(("synthetic-collection", None, None),))

    assert co._Run(req("cu_synthetic"), deps, 0)._store_fetch(fetch, "calendar") == []
    assert not led.conn.execute("SELECT 1 FROM calendar_cancellations").fetchone()
    assert led.conn.execute("SELECT COUNT(*) FROM calendar_records").fetchone()[0] == 1


@pytest.mark.parametrize("other_collection", [False, True])
def test_complete_calendar_listing_without_unlisted_occurrences_still_requires_admission(
        runtime, other_collection):
    from catchup_core.triage import CalendarEvent

    led, deps = runtime
    if other_collection:
        upstream = 'calendar:["other-collection","synthetic-event"]'
        sid, _ = led.upsert_signal(SignalInput(
            "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
        led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
    fetch = co.ConnectorFetch(
        "calendar", "personal", full_listings=(("synthetic-collection", None, None),))
    changes_before = led.conn.total_changes

    with pytest.raises(PermissionDenied, match="scope"):
        co._Run(req("cu_synthetic"), deps, 0)._store_fetch(fetch, "calendar")

    assert led.conn.total_changes == changes_before
    assert led.conn.execute("SELECT COUNT(*) FROM calendar_records").fetchone()[0] == int(other_collection)


def test_complete_calendar_listing_with_new_records_holds_before_effects(runtime):
    led, deps = runtime
    fetch = co.ConnectorFetch(
        "calendar", "personal", (co.ConnectorRecord("event", "synthetic"),),
        full_listings=(("synthetic-collection", None, None),))
    run = co._Run(req("cu_synthetic"), deps, 0)
    changes_before = led.conn.total_changes

    with pytest.raises(PermissionDenied, match="scope"):
        run._store_fetch(fetch, "calendar")

    assert led.conn.total_changes == changes_before
    assert not deps.blobs._root.exists()


def test_explicit_forget_still_erases_synthetic_source_with_scope_hold(runtime):
    led, _ = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
                               "ved", NOW.isoformat(), evidence=((sid, 0, 1),)))

    receipt = led.forget("school_email", "personal", "synthetic-mail")

    assert receipt.deleted_signals == 1
    assert not led.conn.execute("SELECT 1 FROM signals WHERE signal_id=?", (sid,)).fetchone()
    assert not led.conn.execute("SELECT 1 FROM facts WHERE fact_id=?", (fid,)).fetchone()
    assert led.conn.execute(
        "SELECT state FROM deletion_journal WHERE seq=?", (receipt.journal_seq,)).fetchone()[0] == "receipted"


def test_evidence_composition_does_not_conceal_scope_hold(runtime):
    from types import SimpleNamespace

    led, deps = runtime
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
                               "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    result = SimpleNamespace(
        alerts=(SimpleNamespace(fact_id=fid, obligation_key="synthetic"),),
        horizon=(), supporting=(), cross_domain=())

    with pytest.raises(PermissionDenied, match="scope"):
        co._Run(req("cu_synthetic"), deps, 0)._evidence_for(result)


@pytest.mark.parametrize("stage", ["normalize", "extract", "reconcile"])
def test_personal_signal_stages_hold_before_source_payload(runtime, stage):
    led, deps = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.set_signal_subjects(sid, ("ved",))
    state = {"normalize": ProcessingState.FETCHED, "extract": ProcessingState.NORMALIZED,
             "reconcile": ProcessingState.EXTRACTED}[stage]
    led.set_processing_state(sid, state)
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (
                (table == "signals" and column == "evidence_blob_ref")
                or (table == "candidates" and column == "payload_encrypted")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        with pytest.raises(PermissionDenied, match="scope"):
            getattr(co._Run(req("cu_synthetic"), deps, 0), f"_stage_{stage}")()
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("surface", ["scheduler", "calendar", "withdraw", "corpus", "visibility", "candidates"])
def test_signal_dependent_surfaces_hold_before_payload_or_effects(runtime, surface):
    from catchup_core import commands, corpus
    from catchup_core.resources import AdmissionStore, ResourcePolicy
    from catchup_core.triage import CalendarEvent

    led, _ = runtime
    upstream = 'calendar:["synthetic-collection","synthetic-event"]'
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.set_signal_subjects(sid, ("ved",))
    led.set_processing_state(sid, ProcessingState.NORMALIZED)
    led.put_candidates(sid, [{"value": "synthetic"}])
    led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
    scheduler = AdmissionStore(led, ResourcePolicy.from_mapping(
        {"max_requests_per_month": 1, "max_pending_signals": 1}))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (
                (table == "signals" and column == "evidence_blob_ref")
                or (table in ("calendar_records", "candidates") and column == "payload_encrypted")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    changes_before = led.conn.total_changes
    try:
        def _attempt():
            if surface == "scheduler":
                scheduler.select_pending()
            elif surface == "calendar":
                led.calendar_for("ved")
            elif surface == "withdraw":
                led.withdraw_unlisted_calendar_occurrences("calendar", "personal", "synthetic-collection", set())
            elif surface == "corpus":
                corpus.prefill(led, lambda _ref: pytest.fail("unclassified source requested"), "calendar", "synthetic")
            elif surface == "visibility":
                commands._signal_visible(led, sid, "ved")
            else:
                led.candidates_for(sid)

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert led.conn.total_changes == changes_before
    assert payload_reads == []


@pytest.mark.parametrize("surface", ["scheduler", "calendar", "withdrawal", "corpus"])
def test_empty_signal_surfaces_still_require_scope_admission(runtime, surface):
    from catchup_core import corpus
    from catchup_core.resources import AdmissionStore, ResourcePolicy

    led, _ = runtime
    def _attempt():
        if surface == "scheduler":
            policy = ResourcePolicy.from_mapping({"max_requests_per_month": 1, "max_pending_signals": 1})
            AdmissionStore(led, policy).select_pending()
        elif surface == "calendar":
            led.calendar_for("ved")
        elif surface == "withdrawal":
            led.withdraw_unlisted_calendar_occurrences(
                "calendar", "personal", "synthetic-collection", set())
        else:
            corpus.prefill(
                led, lambda _ref: pytest.fail("empty corpus requested source payload"), "calendar", "synthetic")

    with pytest.raises(PermissionDenied, match="scope"):
        _attempt()


def test_evaluation_scope_hold_precedes_outcome_payload_read(runtime):
    from catchup_core import corpus
    from catchup_core.contracts import Stage, StageCheckpoint

    led, _ = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", "synthetic-event", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.record_stage(StageCheckpoint("cu_synthetic", Stage.PRESENTED, "committed"))
    corpus.record_outcome(led, "cu_synthetic", {
        "signals": [sid], "alerts": [], "provider_signal_generations": {},
        "permission_generation": led._permission_generation(),
    }, NOW)
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "catchup_evaluations" and column == "payload":
            payload_reads.append(column)
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        with pytest.raises(PermissionDenied, match="scope"):
            corpus.latest_outcome(led, "cu_synthetic")
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


def test_corpus_label_scope_hold_precedes_file_read(runtime, monkeypatch):
    from pathlib import Path

    from catchup_core import corpus

    led, _ = runtime
    directory = led._path.parent / "corpus"
    directory.mkdir()
    path = directory / "case_synthetic.ae1"
    ciphertext, _ = led._cipher.encrypt(
        b'{"case_id":"case_synthetic","signal_id":"synthetic"}', aad=b"case_synthetic")
    path.write_bytes(ciphertext)
    original = Path.read_bytes
    reads = []

    def track_read(target):
        if target == path:
            reads.append(target)
        return original(target)

    monkeypatch.setattr(Path, "read_bytes", track_read)
    with pytest.raises(PermissionDenied, match="scope"):
        corpus.load_labels(led)
    assert reads == []


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("surface", [
    "scheduler", "calendar", "withdrawal", "fetch", "prefill", "outcome", "labels",
])
def test_scope_hold_precedes_all_queries_enumeration_and_effects(runtime, monkeypatch, surface, populated):
    from pathlib import Path

    from catchup_core import corpus
    from catchup_core.contracts import Stage, StageCheckpoint
    from catchup_core.ledger import PersonalScopeUnavailable
    from catchup_core.resources import AdmissionStore, ResourcePolicy
    from catchup_core.triage import CalendarEvent

    led, deps = runtime
    upstream = 'calendar:["synthetic-collection","synthetic-event"]'
    if populated:
        sid, _ = led.upsert_signal(SignalInput(
            "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
        led.set_processing_state(sid, ProcessingState.NORMALIZED)
        led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
        led.record_stage(StageCheckpoint("cu_control", Stage.PRESENTED, "committed"))
        corpus.record_outcome(led, "cu_control", {
            "signals": [sid], "alerts": [], "provider_signal_generations": {},
            "permission_generation": led._permission_generation(),
        }, NOW)
        label = led._path.parent / "corpus" / "case_synthetic.ae1"
        label.parent.mkdir()
        label.write_bytes(b"synthetic ciphertext")
    else:
        label = None

    scheduler = AdmissionStore(led, ResourcePolicy.from_mapping(
        {"max_requests_per_month": 1, "max_pending_signals": 1}))
    fetch = co.ConnectorFetch(
        "calendar", "personal", full_listings=(("synthetic-collection", None, None),))
    run = co._Run(req("cu_synthetic"), deps, 0)
    before = led.conn.serialize()
    changes_before = led.conn.total_changes
    label_before = label.read_bytes() if label else None
    events = []

    def forbidden(kind):
        def fail(*_args, **_kwargs):
            events.append(kind)
            pytest.fail(f"{kind} occurred before scope admission")
        return fail

    class UnreadablePriorities(tuple):
        def __iter__(self):
            return forbidden("priority read")()

    with monkeypatch.context() as patch:
        if surface in ("scheduler", "calendar", "withdrawal", "fetch"):
            method = ("require_personal_signal_admission" if surface == "scheduler"
                      else "require_personal_calendar_admission")
            original = getattr(led, method)

            def gate():
                events.append("admission")
                original()

            patch.setattr(led, method, gate)
        else:
            original = corpus._require_personal_corpus_admission

            def gate():
                events.append("admission")
                original()

            patch.setattr(corpus, "_require_personal_corpus_admission", gate)
        patch.setattr(Path, "glob", forbidden("enumeration"))
        patch.setattr(Path, "read_bytes", forbidden("file read"))
        patch.setattr(Path, "unlink", forbidden("file deletion"))
        patch.setattr(led._cipher, "decrypt", forbidden("decrypt"))
        patch.setattr(led._cipher, "decrypt_json", forbidden("decrypt"))
        patch.setattr(deps.blobs, "get", forbidden("blob read"))
        patch.setattr(deps.blobs, "put", forbidden("blob write"))
        patch.setattr(deps.blobs, "delete", forbidden("blob deletion"))
        patch.setattr(scheduler, "_claim", forbidden("scheduler claim"))
        patch.setattr(led, "transaction", forbidden("transaction"))
        led.conn.set_trace_callback(lambda _sql: events.append("sql"))
        try:
            def _attempt():
                if surface == "scheduler":
                    scheduler.select_pending(UnreadablePriorities(("synthetic-priority",)),
                                             current_catchup_id="cu_synthetic")
                elif surface == "calendar":
                    led.calendar_for("ved")
                elif surface == "withdrawal":
                    led.withdraw_unlisted_calendar_occurrences(
                        "calendar", "personal", "synthetic-collection", set())
                elif surface == "fetch":
                    run._store_fetch(fetch, "calendar")
                elif surface == "prefill":
                    corpus.prefill(led, forbidden("source payload"), "calendar", "synthetic")
                elif surface == "outcome":
                    corpus.latest_outcome(led, "cu_control")
                else:
                    corpus.load_labels(led)

            with pytest.raises(PersonalScopeUnavailable, match="scope"):
                _attempt()
        finally:
            led.conn.set_trace_callback(None)

    assert events == ["admission"]
    assert led.conn.total_changes == changes_before
    assert led.conn.serialize() == before
    assert (label.read_bytes() if label else None) == label_before
    assert not deps.blobs._root.exists()


def test_signal_visibility_preserves_ordinary_denial_and_success(monkeypatch, runtime):
    from catchup_core import commands

    led, _ = runtime

    def denied(*_args):
        raise PermissionDenied("ordinary invisible Signal")

    monkeypatch.setattr(led, "require_signal_operation", denied)
    assert commands._signal_visible(led, "synthetic", "viewer") is False
    monkeypatch.setattr(led, "require_signal_operation", lambda *_args: None)
    assert commands._signal_visible(led, "synthetic", "viewer") is True


def test_scheduler_scope_hold_propagates_through_coordinator(runtime, monkeypatch):
    from catchup_core.contracts import RunStatus
    from catchup_core.ledger import PersonalScopeUnavailable
    from catchup_core.resources import AdmissionStore, ResourcePolicy

    led, deps = runtime
    deps.admissions = AdmissionStore(led, ResourcePolicy.from_mapping(
        {"max_requests_per_month": 1, "max_pending_signals": 1}))
    gate_calls = []
    selection = []
    original = led.require_personal_signal_admission
    select_pending = deps.admissions.select_pending

    def pass_stage_gate_once():
        gate_calls.append("admission")
        original()

    def observe_selection(*args, **kwargs):
        selection.append("entered")
        try:
            result = select_pending(*args, **kwargs)
        except PersonalScopeUnavailable:
            selection.append("blocked")
            raise
        selection.append("returned")
        return result

    monkeypatch.setattr(led, "require_personal_signal_admission", pass_stage_gate_once)
    monkeypatch.setattr(deps.admissions, "select_pending", observe_selection)
    monkeypatch.setattr(co._Run, "_stage_fetch", lambda _self: ())
    monkeypatch.setattr(co._Run, "_stage_normalize", lambda _self: ())

    result = co.run_catchup(req("cu_scheduler_hold"), deps)

    assert gate_calls == ["admission"]
    assert selection == ["entered", "blocked"]
    assert result.status is RunStatus.BLOCKED
    assert result.failures == (FailureClass.PERMISSION,)
    assert result.reason_unpublished == "personal Signal scope unavailable"
    assert result.briefing_id is None and result.briefing_text is None
    assert not led.conn.execute(
        "SELECT 1 FROM stage_journal WHERE catchup_id=? AND stage='extract'", ("cu_scheduler_hold",)).fetchone()
    assert not led.conn.execute("SELECT 1 FROM resource_admissions").fetchone()
    assert not led.conn.execute("SELECT 1 FROM resource_schedule_signals").fetchone()


@pytest.mark.parametrize("mode", list(ExecutionMode))
@pytest.mark.parametrize("source", ["school_email", "work_calendar"])
@pytest.mark.parametrize("existing_coverage", [False, True])
def test_connector_scope_hold_blocks_fetch_and_acknowledgement(runtime, mode, source, existing_coverage):
    from catchup_core.contracts import RunStatus, Stage

    led, deps = runtime
    calls = []
    if existing_coverage:
        led.set_coverage(source, "synthetic", CoverageStatus.COMPLETE.value,
                         cursor_generation="previous-cursor", catchup_id="cu_previous")
        led.set_source_subjects(source, "synthetic", ("synthetic-subject",))
    tables = ("signals", "source_heads", "source_access", "connector_coverage")
    previous_rows = {
        table: [tuple(row) for row in led.conn.execute(f"SELECT * FROM {table}")]
        for table in tables
    }

    def fetch():
        calls.append("fetch")
        return co.ConnectorFetch(
            source, "synthetic", (co.ConnectorRecord("item", "synthetic payload"),),
            cursor="synthetic-cursor", commit=lambda: calls.append("acknowledge"))

    deps.fetchers = {source: fetch}
    deps.extractor = lambda _: calls.append("extract")
    deps.publisher = lambda *_args: calls.append("publish")
    deps.finalizer = lambda *_args: calls.append("finalize")
    result = co.run_catchup(req("cu_connector_hold", mode=mode, connector_scopes=(source,)), deps)

    assert calls == []
    assert result.status is RunStatus.BLOCKED
    assert result.failures == (FailureClass.PERMISSION,)
    assert result.reason_unpublished == "personal Connector scope unavailable"
    assert result.briefing_id is None and result.briefing_text is None
    for table in tables:
        assert [tuple(row) for row in led.conn.execute(f"SELECT * FROM {table}")] == previous_rows[table]
    assert led.get_cursor(source, "synthetic") == ("previous-cursor" if existing_coverage else None)
    assert not deps.blobs._root.exists()
    stages = {row[0] for row in led.conn.execute(
        "SELECT stage FROM stage_journal WHERE catchup_id=?", ("cu_connector_hold",))}
    assert stages == {Stage.CREATED.value, Stage.PREFLIGHT.value}


def test_connector_scope_hold_precedes_fetch_and_ledger_effects(runtime):
    from catchup_core.ledger import PersonalScopeUnavailable

    led, deps = runtime
    run = co._Run(req("cu_direct_connector_hold"), deps, 0)
    before = led.conn.serialize()
    changes_before = led.conn.total_changes
    calls = []

    def fetch():
        calls.append("fetch")
        raise RuntimeError("synthetic provider failure")

    led.conn.set_trace_callback(lambda _sql: calls.append("sql"))
    try:
        with pytest.raises(PersonalScopeUnavailable, match="personal Connector scope unavailable"):
            run._fetch_one("school_email", fetch)
    finally:
        led.conn.set_trace_callback(None)
    assert calls == []
    assert led.conn.total_changes == changes_before
    assert led.conn.serialize() == before
    assert not deps.blobs._root.exists()


@pytest.mark.parametrize("mode", list(ExecutionMode))
def test_unselected_connector_remains_uninvoked(runtime, mode):
    from catchup_core.contracts import RunStatus, Stage

    led, deps = runtime
    calls = []
    deps.fetchers = {"school_email": lambda: calls.append("fetch")}
    result = co.run_catchup(req("cu_unselected_connector", mode=mode, connector_scopes=("calendar",)), deps)

    assert calls == []
    assert result.status is RunStatus.DEGRADED
    assert "calendar unavailable" in result.briefing_text
    assert led.conn.execute(
        "SELECT 1 FROM stage_journal WHERE catchup_id=? AND stage=?",
        ("cu_unselected_connector", Stage.FETCH.value)).fetchone()
    assert not led.conn.execute("SELECT 1 FROM connector_coverage").fetchone()


def test_simulated_connector_admission_preserves_provider_failure_handling(runtime, monkeypatch):
    """Stub admission only in this synthetic store to exercise the guarded continuation."""
    led, deps = runtime
    run = co._Run(req("cu_simulated_connector_admission"), deps, 0)
    monkeypatch.setattr(led, "require_personal_connector_admission", lambda: None)

    def fetch():
        raise RuntimeError("synthetic provider failure")

    assert run._fetch_one("school_email", fetch) == []
    assert run.failures == [FailureClass.PROVIDER]
    coverage = led.get_coverage("school_email", "unknown")
    assert coverage is not None and coverage["status"] == CoverageStatus.UNAVAILABLE.value


@pytest.mark.parametrize("surface", ["calendar", "withdrawal", "labels", "outcome"])
def test_simulated_admission_preserves_legacy_continuations(runtime, monkeypatch, surface):
    """Stub admission only in this synthetic store to exercise the guarded continuations."""
    from catchup_core import corpus
    from catchup_core.contracts import Stage, StageCheckpoint
    from catchup_core.triage import CalendarEvent

    led, _ = runtime
    monkeypatch.setattr(led, "require_personal_calendar_admission", lambda: None)
    monkeypatch.setattr(corpus, "_require_personal_corpus_admission", lambda: None)
    monkeypatch.setattr(led, "require_signal_operation", lambda *_args: None)
    if surface in ("calendar", "withdrawal"):
        upstream = 'calendar:["synthetic-collection","synthetic-event"]'
        sid, _ = led.upsert_signal(SignalInput(
            "calendar", "personal", upstream, "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
        led.put_calendar("calendar", "personal", upstream, sid, CalendarEvent("synthetic", NOW, NOW))
        if surface == "calendar":
            events = led.calendar_for("ved")
            assert [event.event_id for event in events] == ["synthetic"]
        else:
            assert led.withdraw_unlisted_calendar_occurrences(
                "calendar", "personal", "synthetic-collection", set()) == 1
            assert led.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    elif surface == "labels":
        sid, _ = led.upsert_signal(SignalInput(
            "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
        case = {"case_id": "case_synthetic", "domain": "synthetic", "signal_id": sid}
        corpus.record_label(led, case, {"needs_alert": "no"}, NOW)
        assert [label["case_id"] for label in corpus.load_labels(led)] == ["case_synthetic"]
    else:
        led.record_stage(StageCheckpoint("cu_control", Stage.PRESENTED, "committed"))
        corpus.record_outcome(led, "cu_control", {
            "signals": [], "alerts": [], "provider_signal_generations": {},
            "permission_generation": led._permission_generation(),
        }, NOW)
        assert corpus.latest_outcome(led, "cu_control")["signals"] == []


@pytest.mark.parametrize("surface", ["coverage", "provider", "binding", "fetch"])
def test_global_signal_scope_failure_is_not_treated_as_missing_or_provider_error(runtime, monkeypatch, surface):
    from catchup_core.ledger import PersonalScopeUnavailable

    led, deps = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "calendar", "personal", "synthetic-event", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.set_signal_subjects(sid, ("ved",))
    led.set_source_subjects("calendar", "personal", ("ved",))
    led.set_coverage("calendar", "personal", CoverageStatus.COMPLETE.value)
    run = co._Run(req("cu_synthetic"), deps, 0)
    changes_before = led.conn.total_changes
    fetches = []

    def unavailable():
        fetches.append("fetch")
        raise PersonalScopeUnavailable("personal scope unavailable")

    def _attempt():
        if surface == "coverage":
            led.visible_coverage("ved")
        elif surface == "provider":
            run._provider_signal_current(sid, "ved")
        elif surface == "binding":
            run._personal_signal_is_bound(sid)
        else:
            # Test-only admission bypass exercises the fetcher's own scope denial.
            monkeypatch.setattr(led, "require_personal_connector_admission", lambda: None)
            run._fetch_one("calendar", unavailable)

    with pytest.raises(PermissionDenied, match="scope"):
        _attempt()
    assert led.conn.total_changes == changes_before
    if surface == "fetch":
        assert fetches == ["fetch"]


def test_signal_subject_swap_without_new_binding_denies_before_source_payload(runtime):
    import json

    led, _ = runtime
    led._consent_subjects = frozenset({"kid1"})
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    led.set_signal_subjects(sid, ("kid1",))
    with pytest.raises(PermissionDenied):
        led.require_signal_operation(sid, "ved", "view")
    led.conn.execute("UPDATE signal_access SET subjects=? WHERE signal_id=?", (json.dumps(["ved"]), sid))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "signals" and column == "evidence_blob_ref":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        with pytest.raises(PermissionDenied, match="scope"):
            led.require_signal_operation(sid, "ved", "view")
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("surface", ["completion", "evaluation", "feedback"])
def test_fact_scope_hold_is_not_silently_treated_as_missing_evidence(runtime, surface):
    from catchup_core import curated, heldout, views

    led, _ = runtime
    sid, _ = led.upsert_signal(SignalInput(
        "school_email", "personal", "synthetic-mail", "v1", NOW.isoformat(), "synthetic-hmac", "synthetic-blob"))
    fid = led.put_fact(FactInput("kids_school", "commitment_completed",
        {"record_key": "synthetic", "completed": True}, "ved", NOW.isoformat(),
        provenance=Provenance.USER_CONFIRMED, evidence=((sid, 0, 1),)))
    stamp = led.conn.execute("SELECT recorded_at FROM facts WHERE fact_id=?", (fid,)).fetchone()[0]
    led.upsert_alert("synthetic-obligation", domain="kids_school", primary_subject_id="ved",
                     severity="P2", fact_ids=(fid,))

    def _attempt():
        if surface == "completion":
            curated.completion_fact(led, "ved", f"fact:{fid}@{stamp}", "synthetic")
        elif surface == "evaluation":
            heldout._facts(led, sid, "ved")
        else:
            views._feedback_visible(led, "ved", "synthetic-obligation")

    with pytest.raises(PermissionDenied, match="scope"):
        _attempt()


@pytest.mark.parametrize("surface", ["metadata", "fact", "history"])
def test_fact_subject_change_without_a_new_binding_is_denied_before_payload(runtime, surface):
    led, _ = runtime
    led._consent_subjects = frozenset({"kid1"})
    fid = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
        "kid1", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED))
    with pytest.raises(PermissionDenied):
        led.get_fact_metadata(fid, "ved")
    led.conn.execute("UPDATE facts SET primary_subject_id='ved' WHERE fact_id=?", (fid,))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table in ("facts", "fact_versions") and column == "typed_value":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        def _attempt():
            if surface == "metadata":
                led.get_fact_metadata(fid, "ved")
            elif surface == "fact":
                led.get_fact(fid, "ved")
            else:
                led.fact_history(fid, "ved")

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("fact_bound", [False, True])
@pytest.mark.parametrize("operation", [
    "preview", "confirm", "withdraw", "verified", "requirement", "receipt", "link", "proof",
])
def test_documentary_review_holds_before_source_and_receipt_payload(runtime, fact_bound, operation):
    from catchup_core import fact_access, ids

    led, _ = runtime
    text = "synthetic source"
    marker = ids.content_hmac(KEY, text)
    sid, _ = led.upsert_signal(SignalInput(
        "documents", "manual", "doc_synthetic:p1", "v1", NOW.isoformat(), marker, "synthetic-blob"))
    led.set_signal_subjects(sid, ("ved",))
    led.observe_source_versions([sid], complete=True)
    fact_id = fact_version = None
    if fact_bound:
        fact_id = led.put_fact(FactInput("kids_school", "exam_registration_deadline", "synthetic",
            "ved", NOW.isoformat(), provenance=Provenance.USER_CONFIRMED, evidence=((sid, 0, 9),)))
        fact_version = led.conn.execute("SELECT recorded_at FROM facts WHERE fact_id=?", (fact_id,)).fetchone()[0]
    binding = field_confirmation.FieldBinding(
        "ved", "kids_school", "exam_registration_deadline", '"synthetic"', sid, "v1", (0, 9),
        "doc_synthetic", 1, marker, fact_id, fact_version)
    source_requests = []
    review = field_confirmation.FieldReviewStore(
        led, "ved", lambda: NOW, source_text=lambda signal_id: (source_requests.append(signal_id), text)[1])
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (
                (table == "signals" and column == "evidence_blob_ref")
                or (table in ("document_field_requirements", "document_field_confirmations")
                    and column == "payload")
                or (table == "facts" and column == "typed_value")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    changes_before = led.conn.total_changes
    try:
        def _attempt():
            if operation == "preview":
                review.preview(binding)
            elif operation == "confirm":
                review.confirm(binding, "synthetic-token")
            elif operation == "withdraw":
                review.withdraw(binding, "synthetic-token")
            elif operation == "verified":
                review.verified(binding)
            elif operation == "requirement":
                review._requirement(binding)
            elif operation == "receipt":
                review._confirmation("synthetic", 0)
            elif operation == "link":
                review._link(binding, "synthetic", "confirmed")
            else:
                fact_access.document_link_proof(led.conn, {})

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert led.conn.total_changes == changes_before
    assert source_requests == []
    assert payload_reads == []


@pytest.mark.parametrize("target", ["link", "requirement"])
def test_document_metadata_does_not_read_mismatched_nested_payload(runtime, admitted_documentary_scope, target):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    other, _ = led.upsert_signal(SignalInput(
        "documents", "manual", "doc_other:p1", "v1", NOW.isoformat(), "other-hmac", "other-blob"))
    led.conn.execute(
        "DELETE FROM scope_bindings WHERE object_type='signal' AND object_id=?", (other,))
    table = "document_field_links" if target == "link" else "document_field_requirements"
    led.conn.execute(f"UPDATE {table} SET signal_id=? WHERE fact_id=?", (other, fid))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and (
                (target == "link" and table == "signals" and column == "evidence_blob_ref")
                or (target == "requirement" and table == "document_field_requirements" and column == "payload")):
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        assert led.get_fact_metadata(fid, "ved").status == "unknown"
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("surface", ["metadata", "fact", "review"])
def test_unbound_document_requirement_is_not_read(runtime, admitted_personal_scope, monkeypatch, surface):
    led, deps = runtime
    with monkeypatch.context() as admission:
        admission.setattr(fact_access, "require_documentary_scope", lambda: None)
        admission.setattr(field_confirmation, "require_documentary_scope", lambda: None)
        fid, _ = ingest_candidate(led, deps)
        confirm(led, deps, fid)
    led.conn.execute(
        "UPDATE document_field_requirements SET payload='synthetic-unclassified' WHERE fact_id=?", (fid,))
    payload_reads = []

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "document_field_requirements" and column == "payload":
            payload_reads.append((table, column))
        return sqlite3.SQLITE_OK

    led.conn.set_authorizer(authorize)
    try:
        def _attempt():
            if surface == "metadata":
                led.get_fact_metadata(fid, "ved")
            elif surface == "fact":
                led.get_fact(fid, "ved")
            else:
                field_confirmation.field_command(Namespace(fact_id=fid, apply=None, reopen=False), deps)

        with pytest.raises(PermissionDenied, match="scope"):
            _attempt()
    finally:
        led.conn.set_authorizer(None)
    assert payload_reads == []


@pytest.mark.parametrize("change", ["binding", "marker"])
def test_document_metadata_refuses_scope_changes_during_resolution(
        runtime, admitted_documentary_scope, monkeypatch, change):
    from catchup_core import fact_access

    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    original = fact_access.document_fact_state

    def change_scope(*args):
        state = original(*args)
        if change == "binding":
            led.conn.execute(
                "DELETE FROM scope_bindings WHERE object_type='fact' AND object_id=?", (fid,))
        else:
            led.conn.execute("UPDATE facts SET scope_hmac=? WHERE fact_id=?", ("0" * 64, fid))
        return state

    monkeypatch.setattr(fact_access, "document_fact_state", change_scope)
    with pytest.raises(PermissionDenied, match="scope"):
        led.get_fact_metadata(fid, "ved")


def test_confirmation_does_not_authorize_another_fact_with_identical_value_and_span(
        runtime, admitted_documentary_scope):
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
def test_untrusted_same_upstream_never_promotes_or_disputes_verified_fact(
        runtime, admitted_documentary_scope, method):
    led, deps = runtime
    old, _ = ingest_candidate(led, deps, method="text")
    assert led.get_fact(old, "ved").provenance_class == "verified_source"
    newer, _ = ingest_candidate(led, deps, method=method, value="2026-10-15")
    assert led.get_fact(newer, "ved").status == "unknown"
    assert led.get_fact(old, "ved").status not in ("disputed", "superseded")


def test_replaced_source_can_be_reviewed_and_independent_correction_survives(
        runtime, admitted_documentary_scope):
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


def test_whole_catchup_surfaces_unreviewed_ocr_then_reuses_confirmed_field(
        runtime, admitted_documentary_scope):
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


def test_failed_link_publication_rolls_back_receipt_and_projection(runtime, admitted_documentary_scope):
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


def test_configured_source_class_cannot_launder_ocr_authority(
        runtime, admitted_documentary_scope, monkeypatch):
    led, deps = runtime
    monkeypatch.setattr(co._Run, "_source_class", lambda *_: "documents")
    fid, _ = ingest_candidate(led, deps)
    assert led.conn.execute("SELECT status FROM facts WHERE fact_id=?", (fid,)).fetchone()[0] == "unknown"


@pytest.mark.parametrize("mutation", ["viewer", "shadow", "non_document", "page", "no_head"])
def test_command_refuses_non_owner_shadow_and_unresolved_document_bindings(
        runtime, admitted_documentary_scope, mutation):
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


def test_keyless_guard_fails_closed_without_field_link_schema(runtime, admitted_documentary_scope):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    led.conn.execute("DROP TABLE document_field_links")
    led._cipher = None
    assert led.get_fact_metadata(fid, "ved").status == "unknown"


def test_unrelated_permission_generation_does_not_erase_runtime_receipt(runtime, admitted_documentary_scope):
    led, deps = runtime
    fid, _ = ingest_candidate(led, deps)
    confirm(led, deps, fid)
    grant = led.grant_consent("other", "other", "health", ["view"], "ved")
    led.revoke_consent(grant)
    assert led.get_fact(fid, "ved").status == "current"
    assert led.get_fact_metadata(fid, "ved").status == "current"


@pytest.mark.parametrize("method", ["confidence 99%", True, 1])
def test_connector_record_rejects_nonliteral_document_methods(method):
    with pytest.raises(ValueError, match=r"document_method\ must\ be\ text,\ ocr\ or\ unknown"):
        co.ConnectorRecord("synthetic", "synthetic", document_method=method)