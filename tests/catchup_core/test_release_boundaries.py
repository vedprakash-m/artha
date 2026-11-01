"""Synthetic counterexamples for WP-15; no household or provider access."""
from __future__ import annotations

import io
import json
from argparse import Namespace
from dataclasses import replace

import pytest

from catchup_core import coordinator as co, commands
from catchup_core.contracts import RunStatus
from catchup_core.coordination import evaluate_tasks
from catchup_core.extract import NormalizedSignal, parse_output
from catchup_core.ledger import PermissionDenied
from catchup_core.mcp import handle, serve
from catchup_core.plans import evaluate_plans
from catchup_core.responsibilities import evaluate_responsibilities
from test_release_regressions import CIPHER, NOW, PACK, request
import test_release_regressions as regressions


@pytest.fixture
def world(tmp_path):
    yield from regressions.world.__wrapped__(tmp_path)


@pytest.mark.parametrize("reverse", [False, True])
def test_opaque_versions_in_one_batch_never_cancel_each_other(world, reverse):
    led, _, deps, _ = world
    records = [co.ConnectorRecord("m", "2026-10-14", "v1"), co.ConnectorRecord("m", "2026-10-16", "v2")]
    result = co.run_catchup(request(), deps(records[::-1] if reverse else records))
    assert result.status is RunStatus.DEGRADED
    assert led.unresolved_signals() and "Nothing urgent" not in result.briefing_text
    assert led.conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 2


@pytest.mark.parametrize("version,value", [("v1", "2026-10-14"), ("v2", "2026-10-16")])
def test_fresh_unambiguous_fetch_can_resolve_either_opaque_version(world, version, value):
    led, _, deps, _ = world
    records = [co.ConnectorRecord("m", "2026-10-14", "v1"), co.ConnectorRecord("m", "2026-10-16", "v2")]
    co.run_catchup(request(), deps(records))
    result = co.run_catchup(request("cu_b"), deps([co.ConnectorRecord("m", value, version)]))
    assert result.status is RunStatus.COMPLETE and not led.unresolved_signals()
    assert led.facts_matching("kids_school", "exam_registration_deadline", "kid1")[0].value == value


def test_partial_fetch_still_surfaces_supported_positive_facts(world):
    from catchup_core.contracts import CoverageStatus
    led, _, deps, _ = world
    d = deps()
    d.fetchers = {"school_email": lambda: co.ConnectorFetch("school_email", "acct",
        (co.ConnectorRecord("m", "2026-10-14", "v1"),), CoverageStatus.DEGRADED)}
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED
    assert led.facts_matching("kids_school", "exam_registration_deadline", "kid1")
    assert result.artifact_ids


def test_partial_update_does_not_make_an_old_version_the_permanent_head(world):
    from catchup_core.contracts import CoverageStatus
    led, _, deps, _ = world
    co.run_catchup(request(), deps([co.ConnectorRecord("m", "2026-10-14", "v1")]))
    partial = deps()
    partial.fetchers = {"school_email": lambda: co.ConnectorFetch("school_email", "acct",
        (co.ConnectorRecord("m", "2026-10-16", "v2"),), CoverageStatus.DEGRADED)}
    co.run_catchup(request("cu_b"), partial)
    assert led.facts_matching("kids_school", "exam_registration_deadline", "kid1")[0].value == "2026-10-16"
    co.run_catchup(request("cu_c"), deps([co.ConnectorRecord("m", "2026-10-14", "v1")]))
    assert led.facts_matching("kids_school", "exam_registration_deadline", "kid1")[0].value == "2026-10-14"


def test_complete_source_replacement_resolves_older_content_gaps(world):
    led, _, deps, _ = world
    co.run_catchup(request(), deps([co.ConnectorRecord("m", "2026-10-14", "v1", incomplete=True)]))
    result = co.run_catchup(request("cu_b"), deps([co.ConnectorRecord("m", "2026-10-16", "v2")]))
    assert result.status is RunStatus.COMPLETE and not led.unresolved_signals()


def test_source_head_checks_do_not_scan_every_upstream_for_each_fact(world):
    led, _, _, _ = world
    plan = led.conn.execute("EXPLAIN QUERY PLAN SELECT 1 FROM source_heads WHERE signal_id=? AND complete=1",
                            ("synthetic",)).fetchall()
    assert any("SEARCH" in row[3] for row in plan)


@pytest.mark.parametrize("evidence", [None, False, True, 42, "", "   ", "unknown", "N/A", "pending"])
def test_invalid_completion_evidence_never_closes_any_commitment(evidence):
    day = NOW.date()
    milestones = [{"id": "a", "status": "done", "evidence": evidence},
                  {"id": "b", "prerequisites": ["a"]}]
    states = evaluate_plans([{"id": "G", "milestones": milestones}], day)
    assert [s.state for s in states] == ["unverified", "blocked"]
    assert evaluate_tasks([{"id": "H", "obligation": "RSVP", "owner": "owner", "status": "done",
                            "completion_evidence": evidence}], day)[0].state == "unverified"
    assert evaluate_responsibilities([{"id": "R", "outcome": "Renew", "status": "completed",
                                       "completion_evidence": evidence}], day)[0].state == "unverified"


def test_duplicate_milestone_ids_cannot_fabricate_completion():
    milestones = [{"id": "a", "status": "done", "evidence": "receipt #42"}, {"id": "a"},
                  {"id": "b", "prerequisites": ["a"]}]
    states = evaluate_plans([{"id": "G", "milestones": milestones}], NOW.date())
    assert all(s.state not in ("done", "ready") for s in states)
    assert any(s.state == "invalid" for s in states)


@pytest.mark.parametrize("milestones", [
    [{"id": "a", "prerequisites": "abc"}],
    [{"id": "a", "prerequisites": ["b"]}, {"id": "b", "prerequisites": ["a"]}],
])
def test_invalid_dependency_graph_is_visible(milestones):
    assert any(s.state == "invalid" for s in evaluate_plans([{"milestones": milestones}], NOW.date()))


def test_planning_errors_block_quiet_and_are_evaluated_once(world):
    _, _, deps, _ = world
    d = deps()
    calls = []
    def broken():
        calls.append(1)
        raise PermissionDenied("private details must not escape")
    d.plan_lines = broken
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and len(calls) == 1
    assert "Nothing urgent" not in result.briefing_text and "unavailable" in result.briefing_text
    assert "private details" not in result.briefing_text


@pytest.mark.parametrize("name", [[], {}, 1, None])
def test_malformed_mcp_tool_names_are_protocol_errors(name):
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name}}, None)
    assert response["error"]["code"] == -32602


def test_mcp_uses_bounded_reads_and_recovers_after_oversized_line():
    class Bounded(io.StringIO):
        def __iter__(self):
            raise AssertionError("unbounded iteration")
        def readline(self, size=-1):
            assert 0 < size <= 1_000_001
            return super().readline(size)
    source = Bounded("x" * 2_100_000 + '\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    output = io.StringIO()
    serve(source, output, None)
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 2 and responses[0]["error"]["code"] == -32600
    assert responses[1]["result"] == {}


@pytest.mark.parametrize("text", ["Accommodation approved?", "Approval revoked; it had been approved."])
def test_question_and_revoked_enum_evidence_require_review(text):
    predicate = next(p for p in PACK.predicates.values() if p.type == "enum" and "approved" in p.values)
    raw = {"predicate": predicate.name, "subject_id": "kid1", "value": "approved", "quote": text,
           "span": [0, len(text)]}
    result = parse_output(json.dumps({"complete": True, "candidates": [raw]}), PACK,
                          NormalizedSignal("s", "school_email", text), ["kid1"])
    assert not result.candidates and result.rejections


def test_unprocessed_document_requires_export_consent(world, tmp_path):
    led, _, deps, _ = world
    led._consent_subjects = frozenset({"adult"})
    led.grant_consent("adult", "adult", "documents:manual", ["ingest", "disclose", "view"], "owner")
    directory = tmp_path / "documents"
    directory.mkdir()
    payload = {"schema": "artha.document.v1", "subject": "adult", "pages": ["private"], "original": ""}
    (directory / "doc_test.ae1").write_bytes(CIPHER.encrypt(json.dumps(payload).encode(), aad=b"doc_test")[0])
    target = tmp_path / "export.ae1"
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command="export", path=str(target)), deps(), tmp_path)
    assert not target.exists()


def test_authorized_unprocessed_document_is_in_the_exact_export(world, tmp_path):
    from catchup_core.backup import read_snapshot
    led, _, deps, _ = world
    led._consent_subjects = frozenset({"adult"})
    led.grant_consent("adult", "adult", "documents:manual", ["export"], "owner")
    directory = tmp_path / "documents"
    directory.mkdir()
    data = CIPHER.encrypt(json.dumps({"schema": "artha.document.v1", "subject": "adult"}).encode(),
                          aad=b"doc_test")[0]
    (directory / "doc_test.ae1").write_bytes(data)
    target = tmp_path / "export.ae1"
    commands.execute(Namespace(command="export", path=str(target)), deps(), tmp_path)
    assert read_snapshot(target, CIPHER)["documents/doc_test.ae1"] == data


def test_orphan_evidence_is_never_exported_without_provenance(world, tmp_path):
    _, blobs, deps, _ = world
    blobs.put("orphan", "unattributed material")
    with pytest.raises(PermissionDenied, match="provenance"):
        commands.execute(Namespace(command="export", path=str(tmp_path / "out.ae1")), deps(), tmp_path)


@pytest.mark.parametrize("actor,allowed", [("owner", True), ("other", False)])
def test_capture_export_binds_actor_and_identity(world, tmp_path, actor, allowed):
    led, _, deps, _ = world
    directory = tmp_path / "inbox"
    directory.mkdir()
    (directory / "cap_test.ae1").write_bytes(CIPHER.encrypt(
        json.dumps({"id": "cap_test", "actor": actor, "text": "synthetic"}).encode(), aad=b"cap_test")[0])
    target = tmp_path / "out.ae1"
    if allowed:
        assert commands.execute(Namespace(command="export", path=str(target)), deps(), tmp_path)["export"]
    else:
        with pytest.raises(PermissionDenied):
            commands.execute(Namespace(command="export", path=str(target)), deps(), tmp_path)


def test_new_artifact_during_snapshot_blocks_publication(world, tmp_path):
    from catchup_core.backup import encrypted_snapshot
    from catchup_core.ledger import LedgerError
    led, _, _, _ = world
    def changed(_name, _data):
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox/cap_new.ae1").write_bytes(b"ciphertext")
    target = tmp_path / "snapshot.ae1"
    with pytest.raises(LedgerError, match="inputs changed"):
        encrypted_snapshot(target, led._path, CIPHER, authorize=changed)
    assert not target.exists()


def test_overloaded_planning_inputs_are_visible_and_prevent_quiet(world):
    from catchup_core.runtime import _responsibility_lines
    from pathlib import Path
    _, _, deps, _ = world
    d = deps()
    raw = {"responsibilities": [{"id": f"R{i}", "outcome": "Review", "last_checked": "2026-10-12",
                               "cadence_days": 30} for i in range(51)]}
    d.responsibility_lines = lambda: _responsibility_lines(Path("."), today=NOW.date(),
                                                          reader=lambda _: json.dumps(raw))
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and "deferred" in result.briefing_text
    assert "Nothing urgent" not in result.briefing_text


@pytest.mark.parametrize("change", [{"cadence_days": "often"}, {"next_check": "soon"}])
def test_invalid_responsibility_schedules_remain_visible(change):
    row = {"id": "R", "outcome": "Renew", "last_checked": "2026-10-12", "cadence_days": 30, **change}
    assert any(s.state == "invalid" for s in evaluate_responsibilities([row], NOW.date()))


def test_read_only_goal_observation_does_not_write(world):
    from catchup_core.goals import observe_goals
    led, _, _, _ = world
    before = led.conn.total_changes
    goal = {"id": "G", "title": "Register", "metric": {"kind": "on_time_obligations",
            "predicate": "exam_registration_deadline", "target": 1, "domain": "kids_school"}}
    assert observe_goals(led, [goal], "owner", NOW, persist=False)
    assert led.conn.total_changes == before


def test_read_only_open_enforces_sqlite_boundary(world):
    from catchup_core.ledger import Ledger
    from test_release_regressions import KEY
    import sqlite3
    led, _, _, _ = world
    with Ledger(led._path, id_key=KEY, cipher=CIPHER).open(read_only=True) as read:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read.conn.execute("DELETE FROM signals")


def test_missing_inspection_keys_cannot_be_created():
    from catchup_core.crypto import KeyringKeyProvider, KeyUnavailableError
    from test_runtime import FakeKeyring
    keys = FakeKeyring()
    with pytest.raises(KeyUnavailableError):
        KeyringKeyProvider(backend=keys, create=False).current_version()
    assert not keys.store


def test_emergency_authority_does_not_require_quality_scorecard():
    from catchup_core.policy import validate_local_authority
    validate_local_authority(replace(request(), host="host"), {"release": {"authority_host": "host"}},
                             viewer="owner", hostname="host")
    with pytest.raises(PermissionDenied):
        validate_local_authority(replace(request(), host="other"), {"release": {"authority_host": "host"}},
                                 viewer="owner", hostname="other")


@pytest.mark.parametrize("missing", ["P1", "P2", "negative", "cross_domain", "goal_correction"])
def test_release_requires_each_consequential_review_cohort(missing):
    from catchup_core.policy import _validate_domains
    from test_integrated_surfaces import release_scorecard
    data = release_scorecard()
    data["evaluations"] = [case for case in data["evaluations"]
                           if case.get("severity") != missing and case.get("case_type") != missing]
    with pytest.raises(PermissionDenied):
        _validate_domains(data, ["kids_school"])


@pytest.mark.parametrize("value", [False, True, -1, float("nan"), float("inf")])
def test_release_bounds_reject_nonfinite_and_boolean_measurements(value):
    from catchup_core.policy import _validate_domains
    from test_integrated_surfaces import release_scorecard
    data = release_scorecard()
    data["measurements"]["kids_school"]["monthly_usd"] = value
    with pytest.raises(PermissionDenied):
        _validate_domains(data, ["kids_school"])


def test_enum_grounding_checks_the_sentence_around_a_selective_quote():
    text = "Was the accommodation approved?"
    raw = {"predicate": "accommodation_status", "subject_id": "kid1", "value": "approved", "quote": "approved",
           "span": [text.index("approved"), text.index("approved") + len("approved")]}
    got = parse_output(json.dumps({"complete": True, "candidates": [raw]}), PACK,
                       NormalizedSignal("s", "school_email", text), ["kid1"])
    assert not got.candidates


def test_runtime_preflight_blocks_unknown_footprint_without_using_the_host(world):
    from types import SimpleNamespace
    from catchup_core.runtime import _runtime_preflight, RuntimeConfigError
    led, _, _, _ = world
    with pytest.raises(RuntimeConfigError, match="footprint"):
        _runtime_preflight(led, footprint=lambda: SimpleNamespace(passed=False, message="unverified"))
    _runtime_preflight(led, footprint=lambda: SimpleNamespace(passed=True))


def test_read_only_runtime_never_migrates_recovers_or_enrolls(tmp_path, monkeypatch):
    from catchup_core import runtime as rt
    from catchup_core.contracts import ExecutionMode
    from test_runtime import FakeKeyring, write_cfg
    repo = tmp_path / "repo"
    (repo / "state").mkdir(parents=True)
    write_cfg(repo)
    keys = FakeKeyring()
    req = replace(request(), actor="ved", mode=ExecutionMode.SHADOW)
    options = dict(repo_root=repo, environ={"ARTHA_LOCAL_DIR": str(tmp_path / "local")}, keyring_backend=keys,
                   handler_loader=lambda _: None, auth_loader=lambda _: {})
    deps = rt.build_deps(req, **options)
    deps.ledger.close()
    before_keys = dict(keys.store)
    def forbidden(*_args, **_kw):
        raise AssertionError("inspection tried to mutate")
    for name in ("migrate", "protect_legacy_values", "resume_pending_forgets"):
        monkeypatch.setattr(rt.Ledger, name, forbidden)
    read = rt.build_deps(req, inspection=True, **options)
    try:
        assert read.ledger.conn.total_changes == 0 and keys.store == before_keys
    finally:
        read.ledger.close()


def test_calendar_kind_comes_from_trusted_calendar_configuration():
    from catchup_core.adapters import fetch_via_handler
    from test_adapters import cal, gen
    got = fetch_via_handler("calendar", "family", gen([cal(1, calendar_id="school")]), since="x",
                            calendar_kinds={"school": "school"})
    assert got.records[0].calendar.kind == "school"


def test_malformed_planning_yaml_cannot_look_like_no_responsibilities(world):
    from catchup_core.runtime import _responsibility_lines
    from pathlib import Path
    _, _, deps, _ = world
    d = deps()
    d.responsibility_lines = lambda: _responsibility_lines(Path("."), today=NOW.date(),
                                                          reader=lambda _: "responsibilities: [")
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and "Nothing urgent" not in result.briefing_text


def test_inspection_freshness_never_claims_current_without_a_completed_catchup(world):
    from catchup_core.runtime import inspection_freshness
    led, _, _, _ = world
    metadata = inspection_freshness(led, NOW)
    assert metadata["last_catch_up"] is None and metadata["unseen_since"] == "unknown"


def test_preflight_failure_returns_blocked_without_fetching(world):
    _, _, deps, _ = world
    d = deps()
    d.preflight = lambda: (_ for _ in ()).throw(RuntimeError("footprint unverified"))
    d.fetchers = {"school_email": lambda: pytest.fail("preflight must precede providers")}
    assert co.run_catchup(request(), d).status is RunStatus.BLOCKED


def test_emergency_pause_needs_no_database_or_keyring(tmp_path, monkeypatch):
    from catchup_core.cli import _pause_actions
    from catchup_core.runtime import RuntimeConfig
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "authority")
    cfg = RuntimeConfig({}, [], "owner", policy={"authority_host": "authority"})
    result = _pause_actions(replace(request(), host="authority"), cfg, tmp_path)
    assert result == {"actions_paused": True} and (tmp_path / "config/actions.paused").is_file()
    assert not list(tmp_path.rglob("*.db"))


def test_shared_goals_file_cannot_bypass_an_adults_curated_consent(world, tmp_path):
    led, _, deps, _ = world
    led._consent_subjects = frozenset({"adult"})
    (tmp_path / 'state').mkdir()
    (tmp_path / 'state' / 'goals.md').write_text('goals:\n- {id: legacy, title: Unscoped}\n')
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command="goals"), deps(), tmp_path)


def test_planning_gap_is_visible_even_when_an_urgent_alert_exists(world):
    _, _, deps, _ = world
    d = deps([co.ConnectorRecord("m", "2026-10-14")])
    d.plan_lines = lambda: (_ for _ in ()).throw(ValueError("unreadable"))
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and result.artifact_ids
    assert "Goal plans unavailable" in result.briefing_text


def test_flash_shows_due_commitments_instead_of_claiming_unknown_inputs(world):
    from catchup_core.contracts import BriefingFormat
    _, _, deps, _ = world
    d = deps()
    d.responsibility_lines = lambda: ["R1: check now — Renew registration"]
    result = co.run_catchup(replace(request(), briefing_format=BriefingFormat.FLASH), d)
    assert "Renew registration" in result.briefing_text
    assert "some inputs are incomplete" not in result.briefing_text

# Completion regressions: requirements remain coupled across permissions, recovery and projections.
def test_view_consent_does_not_authorize_correction_or_its_side_effects(world, tmp_path):
    from catchup_core.ledger import FactInput
    from catchup_core import views
    led, _, deps, _ = world
    led._consent_subjects = frozenset({'adult'})
    led.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner')
    fid = led.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-14',
                                'adult', NOW.isoformat()))
    version = led.get_fact(fid, 'owner').recorded_at
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command='correct', fact_id=fid, value='2026-10-16', apply=version), deps(), tmp_path)
    assert not led.conn.execute('SELECT 1 FROM feedback').fetchone()
    with pytest.raises(PermissionDenied):
        views.correct(led, 'owner', fid, '2026-10-16', version)
    led.grant_consent('adult', 'adult', 'kids_school', ['correct'], 'owner')
    views.correct(led, 'owner', fid, '2026-10-16', version)
    assert led.get_fact(fid, 'owner').value == '2026-10-16'


@pytest.mark.parametrize('independent', [False, True])
def test_calendar_cancellation_preserves_independent_support(world, independent):
    from datetime import date
    from catchup_core.ledger import FactInput, SignalInput
    from catchup_core.triage import CalendarEvent
    led, _, _, _ = world
    sid, _ = led.upsert_signal(SignalInput('calendar', 'acct', 'event', 'v1', NOW.isoformat(), 'c1', 'b1'))
    other, _ = led.upsert_signal(SignalInput('school_email', 'acct', 'mail', 'v1', NOW.isoformat(), 'c2', 'b2'))
    evidence = ((sid, 0, 1), (other, 0, 1)) if independent else ((sid, 0, 1),)
    fid = led.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-14',
                                'kid1', NOW.isoformat(), evidence=evidence))
    led.put_calendar('calendar', 'acct', 'event', sid,
                     CalendarEvent('event', date(2026, 10, 14), date(2026, 10, 15), True, 'school'))
    led.cancel_calendar('calendar', 'acct', 'event')
    fact = led.get_fact(fid, 'owner')
    assert fact.status == ('current' if independent else 'unknown')
    if independent:
        assert fact.evidence == ((other, 0, 1),)
    assert led.calendar_for('owner') == []


def test_calendar_ids_and_cancellations_are_scoped_by_calendar(tmp_path, monkeypatch):
    from catchup_core import adapters
    monkeypatch.setenv('ARTHA_LOCAL_DIR', str(tmp_path))
    raw = {'id': 'same', 'calendar_id': 'a', 'source': 'calendar', 'summary': 'School',
           'start': '2026-10-14T09:00:00-07:00', 'end': '2026-10-14T10:00:00-07:00'}
    got = adapters.fetch_via_handler('calendar', 'acct', lambda **_: iter([raw, dict(raw, calendar_id='b'),
        {'id': 'same', 'calendar_id': 'a', 'status': 'cancelled'}]), since='2026-10-01')
    assert len(set(r.upstream_id for r in got.records)) == 2
    assert got.cancelled == (got.records[0].upstream_id,)


def test_curated_change_invalidates_a_resumed_draft(world):
    from catchup_core.contracts import Stage
    _, _, deps, published = world
    current = ['G1: old next step']
    d = deps()
    d.goal_lines = lambda: current
    def crash(stage):
        if stage is Stage.COMMIT:
            raise OSError('synthetic interruption')
    d.fault = crash
    assert co.run_catchup(request(), d).status is RunStatus.BLOCKED
    current = ['G1: corrected next step']
    d.fault = None
    resumed = co.run_catchup(request(), d)
    assert resumed.status is RunStatus.BLOCKED and not published


@pytest.mark.parametrize('record', [
    {'id': 'R', 'outcome': 'Renew', 'allowed_operations': 7},
    {'id': 'R', 'outcome': 'Renew', 'allowed_operations': 'read'},
    {'id': 'R', 'outcome': 'Renew', 'last_checked': '2026-01-01'},
    {'outcome': 'Renew'}, {'id': 'R'},
])
def test_malformed_responsibilities_are_visible_without_crashing(world, record):
    from catchup_core.responsibilities import responsibility_lines
    _, _, deps, _ = world
    states = evaluate_responsibilities([record], NOW.date())
    assert states and any(s.state == 'invalid' for s in states)
    d = deps()
    d.responsibility_lines = lambda: co.ContextLines(responsibility_lines(states), ('Responsibilities need review',))
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and 'Nothing urgent' not in result.briefing_text


def test_invalid_milestone_date_does_not_unlock_dependants():
    states = evaluate_plans([{'id': 'G', 'milestones': [
        {'id': 'a', 'status': 'done', 'evidence': 'receipt #42', 'target_date': 'soon'},
        {'id': 'b', 'prerequisites': ['a']}]}], NOW.date())
    assert states[0].state == 'invalid' and states[1].state == 'blocked'


def test_unresolved_completion_reference_never_unlocks_work():
    states = evaluate_plans([{'id': 'G', 'milestones': [
        {'id': 'a', 'status': 'done', 'evidence': 'banana'}, {'id': 'b', 'prerequisites': ['a']}]}], NOW.date())
    assert [s.state for s in states] == ['unverified', 'blocked']
    assert evaluate_responsibilities([{'id': 'R', 'outcome': 'Renew', 'status': 'completed',
                                      'completion_evidence': 'banana'}], NOW.date())[0].state == 'unverified'
    assert evaluate_tasks([{'id': 'H', 'owner': 'owner', 'obligation': 'RSVP', 'status': 'done',
                            'completion_evidence': 'banana'}], NOW.date())[0].state == 'unverified'


def test_utf8_span_contract_and_mid_character_rejection():
    text = 'é deadline 2026-10-14'
    start = len('é deadline '.encode())
    raw = {'predicate': 'exam_registration_deadline', 'subject_id': 'kid1', 'value': '2026-10-14',
           'quote': '2026-10-14', 'span': [start, len(text.encode())]}
    parsed = parse_output(json.dumps({'complete': True, 'candidates': [raw]}), PACK,
                          NormalizedSignal('s', 'school_email', text), ['kid1'])
    assert len(parsed.candidates) == 1 and parsed.candidates[0].span == tuple(raw['span'])
    raw.update(quote='é', span=[0, 1])
    assert parse_output(json.dumps({'complete': True, 'candidates': [raw]}), PACK,
                        NormalizedSignal('s', 'school_email', text), ['kid1']).rejections


def test_explicit_completion_binds_the_record_and_survives_reopen(world, tmp_path):
    from catchup_core import runtime
    led, _, deps, _ = world
    state = tmp_path / 'state'; state.mkdir()
    path = state / 'goals.md'
    path.write_text('goals:\n- id: G\n  owner: owner\n  status: active\n  milestones:\n  - {id: a, title: Gather}\n  - {id: b, title: Submit, prerequisites: [a]}\n')
    d = deps()
    args = Namespace(command='complete', record='milestone:G:a', apply=None)
    preview = commands.execute(args, d, tmp_path)
    assert not led.conn.execute("SELECT 1 FROM facts WHERE predicate='commitment_completed'").fetchone()
    args.apply = preview['apply_token']
    result = commands.execute(args, d, tmp_path)
    assert result['completion_reference'].startswith('fact:')
    led.close()
    led.open()
    got = runtime._plan_lines(state, today=NOW.date(), ledger=led, viewer='owner')
    assert any('next step: Submit' in line for line in got.lines) and not any('next step: Gather' in line for line in got.lines)
    path.write_text(path.read_text().replace('title: Gather', 'title: Gather different documents'))
    got = runtime._plan_lines(state, today=NOW.date(), ledger=led, viewer='owner')
    assert any('waits for Gather different documents' in line for line in got.lines)
    with pytest.raises(ValueError, match='changed'):
        commands.execute(args, d, tmp_path)


def test_completion_cannot_impersonate_another_owner(world, tmp_path):
    led, _, deps, _ = world
    state = tmp_path / 'state'; state.mkdir()
    (state / 'responsibilities.md').write_text('responsibilities:\n- {id: R, owner: adult, outcome: Renew}\n')
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command='complete', record='responsibility:R', apply=None), deps(), tmp_path)
    assert not led.conn.execute('SELECT 1 FROM facts').fetchone()


def test_publication_rechecks_permissions_after_the_last_stage_boundary(world):
    from catchup_core.contracts import Stage
    led, _, deps, published = world
    grant = led.grant_consent('adult', 'adult', 'kids_school', ['view'], 'owner')
    d = deps()
    d.fault = lambda stage: led.revoke_consent(grant) if stage is Stage.COMMIT else None
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.BLOCKED and not published


def test_publication_rechecks_curated_generation_even_when_rendered_text_is_unchanged(world):
    from catchup_core.contracts import Stage
    _, _, deps, published = world
    generation = ['first']
    d = deps()
    d.context_version = lambda: generation[0]
    def mutate(stage):
        if stage is Stage.COMMIT:
            generation[0] = 'changed'
    d.fault = mutate
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.BLOCKED and not published


@pytest.mark.parametrize('reverse', [False, True])
def test_ambiguous_calendar_versions_never_choose_a_conflict_projection(world, reverse):
    from datetime import date
    from catchup_core.triage import CalendarEvent
    led, _, deps, _ = world
    records = [co.ConnectorRecord('same', 'travel A', 'v1', calendar=CalendarEvent('same', date(2026,10,14),
        date(2026,10,15), True, 'travel')), co.ConnectorRecord('same', 'travel B', 'v2',
        calendar=CalendarEvent('same', date(2026,10,16), date(2026,10,17), True, 'travel'))]
    d = deps()
    d.fetchers = {'calendar': lambda: co.ConnectorFetch('calendar', 'acct', tuple(records[::-1] if reverse else records))}
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and led.calendar_for('owner') == []


def test_extractor_command_cannot_launch_a_tool_capable_cli():
    from catchup_core.runtime import command_extractor
    launches = []
    with pytest.raises(RuntimeError, match='tool-less'):
        command_extractor('some-model --allow-shell', runner=lambda *a, **k: launches.append(a))('private')
    assert launches == []


def test_approved_pattern_extractor_has_no_workspace_or_credential_environment(monkeypatch):
    import sys
    from types import SimpleNamespace
    from catchup_core.runtime import command_extractor
    monkeypatch.setenv('SYNTHETIC_PROVIDER_KEY', 'must-not-enter-child')
    def run(argv, **kwargs):
        assert argv[0] == sys.executable and '-I' in argv
        assert 'SYNTHETIC_PROVIDER_KEY' not in kwargs['env'] and 'HOME' not in kwargs['env']
        assert kwargs['cwd'] != str(__import__('pathlib').Path.cwd())
        return SimpleNamespace(returncode=0, stdout='{"candidates":[],"complete":false}')
    assert 'false' in command_extractor('python3 scripts/catchup_core/extractor_cmd.py', runner=run)('private')


@pytest.mark.parametrize('record', ['unknown:R', 'milestone:G', 'milestone:G:a:extra', 'responsibility:missing'])
def test_completion_rejects_missing_or_malformed_identities(world, tmp_path, record):
    (tmp_path / 'state').mkdir()
    with pytest.raises(ValueError):
        commands.execute(Namespace(command='complete', record=record, apply=None), world[2](), tmp_path)


@pytest.mark.parametrize('apply', ['', 'garbage', '0:wrong', '999999999999:wrong', 'no:mac'])
def test_completion_invalid_or_expired_preview_never_writes(world, tmp_path, apply):
    state = tmp_path / 'state'; state.mkdir()
    (state / 'responsibilities.md').write_text('responsibilities:\n- {id: R, owner: owner, outcome: Renew}\n')
    with pytest.raises(ValueError):
        commands.execute(Namespace(command='complete', record='responsibility:R', apply=apply), world[2](), tmp_path)
    assert not world[0].conn.execute('SELECT 1 FROM facts').fetchone()


def test_completion_receipt_is_idempotent_and_revocation_invalidates_it(world, tmp_path):
    from catchup_core.curated import completion_key, verify_completion
    from catchup_core.contracts import FactStatus
    state = tmp_path / 'state'; state.mkdir()
    row = {'id': 'R', 'owner': 'owner', 'outcome': 'Renew'}
    (state / 'responsibilities.md').write_text('responsibilities:\n- {id: R, owner: owner, outcome: Renew}\n')
    d = world[2]()
    args = Namespace(command='complete', record='responsibility:R', apply=None)
    args.apply = commands.execute(args, d, tmp_path)['apply_token']
    first = commands.execute(args, d, tmp_path)
    assert commands.execute(args, d, tmp_path) == first
    key = completion_key('responsibility', row)
    reference = first['completion_reference']
    assert verify_completion(world[0], 'owner', reference, key)
    assert not verify_completion(world[0], 'owner', reference, 'another-record')
    assert not verify_completion(world[0], 'owner', reference + '-wrong-version', key)
    assert not verify_completion(world[0], 'stranger', reference, key)
    fid = reference[5:].split('@')[0]
    world[0].set_fact_status(fid, FactStatus.RETRACTED)
    assert not verify_completion(world[0], 'owner', reference, key)
    assert not verify_completion(world[0], 'owner', None, key)


@pytest.mark.parametrize('record', [
    {'id': 'G', 'owner': 'owner', 'milestones': 'bad'},
    {'id': 'G', 'owner': 'owner', 'milestones': []},
    {'id': 'G', 'owner': 'owner', 'milestones': [{'id':'a'}, {'id':'a'}]},
])
def test_completion_rejects_ambiguous_milestones(world, tmp_path, record):
    import yaml
    state = tmp_path / 'state'; state.mkdir()
    (state / 'goals.md').write_text(yaml.safe_dump({'goals': [record]}))
    with pytest.raises(ValueError):
        commands.execute(Namespace(command='complete', record='milestone:G:a', apply=None), world[2](), tmp_path)


def test_completion_subject_must_be_resolved(world, tmp_path):
    state = tmp_path / 'state'; state.mkdir()
    (state / 'responsibilities.md').write_text('responsibilities:\n- {id: R, owner: owner, subject: unknown, outcome: Renew}\n')
    world[0]._known_subjects = frozenset({'owner'})
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command='complete', record='responsibility:R', apply=None), world[2](), tmp_path)


@pytest.mark.parametrize('unit,text,start,end,expected', [
    ('utf8-byte', 'é🙂X', 0, 2, 'é'), ('utf8-byte', 'é🙂X', 2, 6, '🙂'),
    ('unicode-codepoint', 'é🙂X', 1, 2, '🙂'),
])
def test_evidence_coordinate_versions_remain_readable(unit, text, start, end, expected):
    from catchup_core.extract import evidence_slice
    assert evidence_slice(text, start, end, unit) == expected


@pytest.mark.parametrize('unit,start,end', [('unknown',0,1), ('utf8-byte',-1,1), ('utf8-byte',1,2)])
def test_invalid_evidence_coordinates_fail_closed(unit, start, end):
    from catchup_core.extract import evidence_slice
    with pytest.raises((ValueError, UnicodeError)):
        evidence_slice('é', start, end, unit)


@pytest.mark.parametrize('legacy', [False, True])
def test_chunk_rebasing_and_why_share_utf8_or_legacy_coordinates(world, legacy):
    from catchup_core import views
    from catchup_core.ledger import SignalInput
    led, blobs, deps, _ = world
    text = 'é' * 7700 + ' deadline 2026-10-14'
    if legacy:
        ref = blobs.put('legacy', text)
        sid, _ = led.upsert_signal(SignalInput('school_email', 'acct', 'm', 'v1', NOW.isoformat(), 'h', ref))
        led.conn.execute("UPDATE signals SET span_unit='unicode-codepoint' WHERE signal_id=?", (sid,))
    def model(prompt):
        content = prompt.rsplit('<untrusted>\n', 1)[1].rsplit('\n</untrusted>', 1)[0]
        if '2026-10-14' not in content:
            return '{"complete":true,"candidates":[]}'
        before = content[:content.index('2026-10-14')]
        start = len(before.encode())
        return json.dumps({'complete':True,'candidates':[{'predicate':'exam_registration_deadline',
            'subject_id':'kid1','value':'2026-10-14','quote':'2026-10-14','span':[start,start+10]}]})
    d = deps() if legacy else deps([co.ConnectorRecord('m', text, 'v1')])
    d.extractor = model
    result = co.run_catchup(request(), d)
    assert result.artifact_ids
    for fid in result.artifact_ids:
        assert all(e['quote'] == '2026-10-14' for e in views.explain(led, blobs, 'owner', fid)['evidence'])


def test_live_and_cancelled_calendar_in_same_batch_is_ambiguous(world):
    from datetime import date
    from catchup_core.triage import CalendarEvent
    led, _, deps, _ = world
    d = deps()
    rec = co.ConnectorRecord('same', 'travel', 'v1', calendar=CalendarEvent('same', date(2026,10,14),
        date(2026,10,15), True, 'travel'))
    d.fetchers = {'calendar': lambda: co.ConnectorFetch('calendar', 'acct', (rec,), cancelled=('same',))}
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.DEGRADED and not led.calendar_for('owner')


def test_calendar_payload_changes_cannot_reuse_an_evidence_version(world):
    from datetime import date
    from catchup_core.triage import CalendarEvent
    led, _, deps, _ = world
    first = CalendarEvent('e', date(2026,10,14), date(2026,10,15), True, 'travel')
    second = CalendarEvent('e', date(2026,10,16), date(2026,10,17), True, 'travel')
    for cid, event in [('cu_a', first), ('cu_b', second)]:
        d = deps()
        d.fetchers = {'calendar': lambda: co.ConnectorFetch('calendar', 'acct',
            (co.ConnectorRecord('e', 'same text', 'v1', calendar=event),))}
        co.run_catchup(request(cid), d)
    assert led.conn.execute('SELECT count(*) FROM signals').fetchone()[0] == 2


def test_legacy_calendar_erasure_survives_calendar_namespacing(world):
    led = world[0]
    led.forget('calendar', 'acct', 'native')
    assert led.is_tombstoned('calendar', 'acct', 'calendar:["new-scope","native"]')
    for malformed in ['calendar:broken', 'calendar:1', 'calendar:["a"]', 'calendar:[1,2]']:
        assert not led.is_tombstoned('calendar', 'acct', malformed)


def test_cancelled_event_does_not_override_explicit_confirmation(world):
    from datetime import date
    from catchup_core.contracts import Provenance
    from catchup_core.ledger import FactInput, SignalInput
    from catchup_core.triage import CalendarEvent
    led = world[0]
    sid, _ = led.upsert_signal(SignalInput('calendar', 'acct', 'e', 'v1', NOW.isoformat(), 'h', 'ref'))
    fid = led.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-14', 'kid1',
        NOW.isoformat(), provenance=Provenance.USER_CONFIRMED, evidence=((sid,0,1),)))
    led.put_calendar('calendar', 'acct', 'e', sid, CalendarEvent('e', date(2026,10,14), date(2026,10,15), True, 'school'))
    led.cancel_calendar('calendar', 'acct', 'e')
    assert led.get_fact(fid, 'owner').status == 'current'


def test_completion_requires_co_subject_correction_permission(world, tmp_path):
    from catchup_core.curated import completion_key, verify_completion
    led = world[0]
    led._consent_subjects = frozenset({'adult'})
    led.grant_consent('adult', 'adult', 'curated:responsibilities', ['ingest','disclose','view'], 'owner')
    state = tmp_path / 'state'; state.mkdir()
    (state / 'responsibilities.md').write_text('responsibilities:\n- {id: R, owner: owner, subject: kid1, co_subjects: [adult], outcome: Renew}\n')
    with pytest.raises(PermissionDenied):
        commands.execute(Namespace(command='complete', record='responsibility:R', apply=None), world[2](), tmp_path)
    grant = led.grant_consent('adult', 'adult', 'kids_school', ['correct', 'view'], 'owner')
    args = Namespace(command='complete', record='responsibility:R', apply=None)
    args.apply = commands.execute(args, world[2](), tmp_path)['apply_token']
    reference = commands.execute(args, world[2](), tmp_path)['completion_reference']
    key = completion_key('responsibility', {'id': 'R', 'owner': 'owner', 'subject': 'kid1',
                                          'co_subjects': ['adult'], 'outcome': 'Renew'})
    assert verify_completion(led, 'owner', reference, key)
    led.revoke_consent(grant)
    assert not verify_completion(led, 'owner', reference, key)


def test_process_death_during_calendar_withdrawal_recovers_from_journal(world):
    import subprocess
    import sys
    from pathlib import Path
    from datetime import date
    from catchup_core.ledger import FactInput, SignalInput
    from catchup_core.triage import CalendarEvent
    led, _, deps, _ = world
    sid, _ = led.upsert_signal(SignalInput('calendar', 'acct', 'e', 'v1', NOW.isoformat(), 'h', 'ref'))
    fid = led.put_fact(FactInput('kids_school', 'exam_registration_deadline', '2026-10-14', 'kid1',
                                NOW.isoformat(), evidence=((sid,0,1),)))
    led.put_calendar('calendar', 'acct', 'e', sid, CalendarEvent('e', date(2026,10,14), date(2026,10,15), True, 'school'))
    source = Path(__file__).resolve().parents[2]
    code = '''import os,sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from catchup_core.ledger import Ledger
from test_release_regressions import KEY,CIPHER
with Ledger(sys.argv[3], id_key=KEY, cipher=CIPHER).open() as ledger:
    ledger.conn.create_function('crash', 0, lambda: os._exit(23))
    ledger.conn.execute('CREATE TEMP TRIGGER crash_write BEFORE INSERT ON fact_versions BEGIN SELECT crash(); END')
    ledger.cancel_calendar('calendar', 'acct', 'e')
'''
    led.close()
    result = subprocess.run([sys.executable, '-c', code, str(source/'scripts'), str(source/'tests/catchup_core'),
                             str(led._path)], capture_output=True, timeout=15)
    led.open()
    assert result.returncode == 23, result.stderr.decode()
    assert led.conn.execute('SELECT pending FROM calendar_cancellations').fetchone()[0] == 1
    with pytest.raises(PermissionDenied):
        led.get_fact(fid, 'owner')
    assert led.calendar_for('owner') == []
    co.run_catchup(request(), deps())
    assert led.get_fact(fid, 'owner').status == 'unknown'
    assert led.calendar_for('owner') == []
    assert not led.conn.execute('SELECT 1 FROM calendar_cancellations WHERE pending=1').fetchone()


@pytest.mark.parametrize('text,kind', [('not structured', 'goals'), ('- {id: G}', 'goals'),
                                     ('- id: null\n  status: open\n', 'open_items')])
def test_malformed_curated_structure_never_means_no_commitments(text, kind):
    from catchup_core.curated import records
    with pytest.raises(ValueError, match='coverage'):
        records(text, kind)


def test_publication_rechecks_fact_version_after_composition(world):
    from catchup_core import views
    from catchup_core.contracts import Stage
    led, _, deps, published = world
    d = deps([co.ConnectorRecord('m', '2026-10-14')])
    def correct_at_boundary(stage):
        if stage is Stage.COMMIT:
            row = led.conn.execute('SELECT fact_id FROM facts').fetchone()
            fact = led.get_fact(row[0], 'owner')
            views.correct(led, 'owner', row[0], '2026-10-16', fact.recorded_at)
    d.fault = correct_at_boundary
    result = co.run_catchup(request(), d)
    assert result.status is RunStatus.BLOCKED and not published


def test_storage_inspection_refuses_integrity_failure(world, monkeypatch):
    from catchup_core.runtime import _storage_preflight, RuntimeConfigError
    monkeypatch.setattr(world[0], 'integrity_check', lambda: False)
    with pytest.raises(RuntimeConfigError, match='integrity'):
        _storage_preflight(world[0])


def test_storage_inspection_refuses_nonprivate_directory(world):
    import os
    from catchup_core.runtime import _storage_preflight, RuntimeConfigError
    if os.name == 'nt':
        pytest.skip('POSIX directory contract')
    path = world[0]._path.parent
    previous = path.stat().st_mode & 0o777
    try:
        path.chmod(0o777)
        with pytest.raises(RuntimeConfigError, match='permissions'):
            _storage_preflight(world[0])
    finally:
        path.chmod(previous)


def test_milestone_capacity_is_a_visible_gap_not_a_complete_plan():
    from catchup_core.plans import plan_lines
    states = evaluate_plans([{'id':'G','milestones':[{'id':f'm{i}'} for i in range(51)]}], NOW.date())
    assert states[0].state == 'invalid' and states[0].milestone_id == 'deferred'
    assert any('review the complete plan' in line for line in plan_lines(states))


@pytest.mark.parametrize('subjects', [None, 'adult', [False], ['']])
def test_completion_rejects_malformed_co_subjects(subjects):
    from catchup_core.curated import completion_subjects
    with pytest.raises(ValueError, match='subjects'):
        completion_subjects({'co_subjects': subjects}, 'owner')


def test_document_export_with_unknown_subject_never_writes(world, tmp_path):
    led, _, deps, _ = world
    directory = tmp_path / 'documents'; directory.mkdir()
    payload = {'schema':'artha.document.v1','subject':None}
    (directory/'doc_unknown.ae1').write_bytes(CIPHER.encrypt(json.dumps(payload).encode(), aad=b'doc_unknown')[0])
    target = tmp_path / 'export.ae1'
    with pytest.raises(PermissionDenied, match='subject'):
        commands.execute(Namespace(command='export', path=str(target)), deps(), tmp_path)
    assert not target.exists()


def test_unknown_replay_artifact_never_enters_export(world, tmp_path):
    directory = tmp_path / 'replay'; directory.mkdir()
    (directory/'fbk_unknown.ae1').write_bytes(b'opaque ciphertext')
    target = tmp_path / 'export.ae1'
    with pytest.raises(PermissionDenied, match='provenance'):
        commands.execute(Namespace(command='export', path=str(target)), world[2](), tmp_path)
    assert not target.exists()


def test_goal_completion_version_changes_invalidate_identical_resumed_prose(world):
    from catchup_core.ledger import FactInput
    from catchup_core.contracts import Stage, Provenance
    led, _, deps, published = world
    fid = led.put_fact(FactInput('kids_school','commitment_completed',{'completed':True},'owner',NOW.isoformat(),
                                provenance=Provenance.USER_CONFIRMED))
    current = [led.get_fact(fid,'owner').recorded_at]
    d = deps()
    d.goal_lines = lambda: co.ContextLines(['Goal: 1 milestone confirmed'], fact_versions={fid:current[0]})
    def interrupt(stage):
        if stage is Stage.COMMIT:
            raise OSError('interrupted before publication')
    d.fault = interrupt
    assert co.run_catchup(request(),d).status is RunStatus.BLOCKED
    led.put_fact(FactInput('kids_school','commitment_completed',{'completed':True},'owner',NOW.isoformat(),
                          provenance=Provenance.USER_CONFIRMED), fact_id=fid)
    current[0] = led.get_fact(fid,'owner').recorded_at
    d.fault = None
    assert co.run_catchup(request(),d).status is RunStatus.BLOCKED
    assert not published


def test_source_replacement_preserves_independent_support_and_history(world):
    from catchup_core.ledger import SignalInput
    led,blobs,make,_=world
    co.run_catchup(request('replace_initial'),make([co.ConnectorRecord('m','2026-10-14','v1')]))
    fact=led.facts_matching('kids_school','exam_registration_deadline','kid1')[0]
    old=led.fact_history(fact.fact_id,'owner',fact.recorded_at)
    ref=blobs.put('independent-note','2026-10-14')
    sid,_=led.upsert_signal(SignalInput('parent_note','owner','note','v1',NOW.isoformat(),'independent',ref))
    led.add_evidence(fact.fact_id,sid,0,10)
    co.run_catchup(request('replace_withdrawn'),make([co.ConnectorRecord('m','Registration withdrawn','v2')]))
    current=led.get_fact(fact.fact_id,'owner')
    assert current.status=='current' and current.value=='2026-10-14'
    assert current.evidence==((sid,0,10),)
    assert led.fact_history(fact.fact_id,'owner',fact.recorded_at)==old


def test_new_completion_after_composition_cannot_publish_stale_goal_progress(world,tmp_path):
    from catchup_core import runtime
    from catchup_core.contracts import Stage,Provenance
    from catchup_core.curated import completion_key,milestone_record
    from catchup_core.ledger import FactInput
    import yaml
    led,_,make,published=world
    goal={'id':'G','owner':'owner','title':'Finish','status':'active','metric':{'kind':'milestones'},'milestones':[{'id':'a'}]}
    text=yaml.safe_dump({'goals':[goal]})
    deps=make()
    deps.goal_lines=lambda:runtime._goal_lines(tmp_path,reader=lambda _:text,ledger=led,now=NOW)
    def insert_completion(stage):
        if stage is Stage.COMMIT:
            key=completion_key('milestone',milestone_record(goal,goal['milestones'][0]))
            led.put_fact(FactInput('kids_school','commitment_completed',{'record_key':key,'completed':True},'owner',
                NOW.isoformat(),provenance=Provenance.USER_CONFIRMED,obligation_key=key))
    deps.fault=insert_completion
    result=co.run_catchup(request('new_completion'),deps)
    assert result.status is RunStatus.BLOCKED and not published


@pytest.mark.parametrize('boundary', ['before-start', 'before-fetch', 'before-extract', 'before-commit'])
def test_current_runtime_policy_is_rechecked_before_work_and_publication(world, boundary):
    from catchup_core.contracts import Stage
    from catchup_core.ledger import LedgerError
    _, _, make_deps, published = world
    deps = make_deps()
    changed = [boundary == 'before-start']
    def validate():
        if changed[0]:
            raise LedgerError('runtime inputs changed; start a new Catch-up')
    deps.validate_inputs = validate
    stage = {'before-fetch': Stage.FETCH, 'before-extract': Stage.EXTRACT, 'before-commit': Stage.COMMIT}.get(boundary)
    deps.fault = lambda current: changed.__setitem__(0, True) if current is stage else None
    if boundary in {'before-start', 'before-fetch'}:
        deps.fetchers = {'school_email': lambda: pytest.fail('stale policy contacted a Connector')}
    if boundary != 'before-commit':
        deps.extractor = lambda _: pytest.fail('stale policy disclosed a model request')
    result = co.run_catchup(request(), deps)
    assert result.status is RunStatus.BLOCKED and not published
    assert 'inputs changed' in result.reason_unpublished


def test_runtime_policy_is_rechecked_between_connectors(world):
    from catchup_core.ledger import LedgerError
    _, _, make_deps, published = world
    deps = make_deps()
    changed = []
    def first():
        changed.append(True)
        return co.ConnectorFetch('first', 'acct', ())
    def validate():
        if changed:
            raise LedgerError('runtime inputs changed')
    deps.validate_inputs = validate
    deps.fetchers = {'first': first, 'second': lambda: pytest.fail('second Connector used stale policy')}
    assert co.run_catchup(request(), deps).status is RunStatus.BLOCKED and not published


def test_runtime_policy_is_rechecked_between_model_disclosures(world):
    from catchup_core.ledger import LedgerError
    _, _, make_deps, published = world
    deps = make_deps([co.ConnectorRecord('one', '2026-10-14', 'v1'), co.ConnectorRecord('two', '2026-10-15', 'v1')])
    original = deps.extractor
    disclosed = []
    def extract(prompt):
        assert not disclosed, 'a second model request used stale policy'
        answer = original(prompt)
        disclosed.append(True)
        return answer
    def validate():
        if disclosed:
            raise LedgerError('runtime inputs changed')
    deps.extractor, deps.validate_inputs = extract, validate
    result = co.run_catchup(request(), deps)
    assert result.status is RunStatus.BLOCKED and len(disclosed) == 1 and not published
