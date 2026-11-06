"""Real command paths over isolated encrypted storage; no external effects."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from argparse import Namespace
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from catchup_core import adapters, cli, commands, coordinator as co, domain_pack as dp, policy, runtime, views
from catchup_core.contracts import CatchupRequest, ExecutionMode, FactStatus, RunStatus
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.ledger import FactInput, Ledger, LedgerError, PermissionDenied, SignalInput
from catchup_core.triage import CalendarEvent
from test_user_surfaces import CIPHER, KEY, Keyring
from catchup_core.blobs import EncryptedBlobStore

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs/kids_school.yaml")
MODEL_IDENTITY = {'provider': 'synthetic', 'model': 'offline-extractor', 'revision': 'fixture-1',
                  'retention_policy': 'synthetic-no-retention'}


@pytest.mark.parametrize('dependency', ['user_profile.yaml', 'connectors.yaml'])
def test_release_cohort_rejects_changed_effective_workspace_inputs(tmp_path, dependency):
    directory = tmp_path / 'config'
    directory.mkdir()
    (directory / 'user_profile.yaml').write_text('household: {type: single}\n')
    (directory / 'connectors.yaml').write_text('connectors: {}\n')
    config = {'model_identity': MODEL_IDENTITY, 'household_from_profile': True}
    before = policy.evaluation_cohort(config, ['kids_school'], workspace=tmp_path)
    result = {'cohort': before, 'evaluations': [{'cohort': before, 'catchup_id': 'synthetic'}],
              'measurements': {'kids_school': {'cohort': before}}}
    policy._validate_evaluation_cohort(result, before)
    path = directory / dependency
    path.write_text(path.read_text() + '# changed effective dependency\n')
    after = policy.evaluation_cohort(config, ['kids_school'], workspace=tmp_path)
    assert before['configuration'] != after['configuration']
    with pytest.raises(PermissionDenied, match='cohort'):
        policy._validate_evaluation_cohort(result, after)


@pytest.mark.parametrize('dependency', ['connectors.yaml', 'user_profile.yaml'])
def test_runtime_refuses_changed_workspace_inputs_before_fetch_or_publication(tmp_path, dependency):
    from test_runtime import FakeKeyring, write_cfg

    root = tmp_path / 'repo'
    (root / 'state').mkdir(parents=True)
    write_cfg(root, subjects=['kid1'], consent_required=[], viewer='owner', household_from_profile=True)
    connectors = {'connectors': {'gmail': {'fetch': {'handler': 'synthetic.initial'}}}}
    profile = {'family': {'primary_user': {'name': 'Synthetic Owner'},
                          'children': [{'id': 'kid1', 'name': 'Synthetic Child', 'dob': '2009-01-01'}]}}
    (root / 'config/connectors.yaml').write_text(json.dumps(connectors))
    (root / 'config/user_profile.yaml').write_text(json.dumps(profile))
    effects = []

    def unexpected_effect(*args, **kwargs):
        effects.append((args, kwargs))
        raise AssertionError('stale runtime reached fetch, model or publication')

    request = CatchupRequest('cu_changed_workspace_input', 'owner', 'cli', mode=ExecutionMode.SHADOW)
    deps = runtime.build_deps(request, repo_root=root,
        environ={'ARTHA_LOCAL_DIR': str(tmp_path / 'private')}, keyring_backend=FakeKeyring(),
        handler_loader=unexpected_effect, auth_loader=unexpected_effect, now=lambda: NOW)
    try:
        deps = replace(deps, preflight=unexpected_effect, extractor=unexpected_effect,
                       publisher=unexpected_effect)
        deps.validate_inputs()
        assert deps.subjects == ['owner', 'kid1']
        assert deps.ledger._consent_subjects == frozenset()
        before = {path.name: path.read_bytes() for path in (root / 'config').iterdir()}
        if dependency == 'connectors.yaml':
            connectors['connectors']['gmail']['fetch']['handler'] = 'synthetic.changed'
            changed = connectors
        else:
            profile['family']['children'][0]['dob'] = '2000-01-01'
            changed = profile
        (root / 'config' / dependency).write_text(json.dumps(changed))
        assert {path.name for path in (root / 'config').iterdir()
                if path.read_bytes() != before[path.name]} == {dependency}

        with pytest.raises(LedgerError, match='runtime inputs changed; start a new Catch-up'):
            deps.validate_inputs()
        result = co.run_catchup(request, deps)
        assert result.status is RunStatus.BLOCKED
        assert result.reason_unpublished == 'runtime inputs changed; start a new Catch-up'
        assert result.briefing_id is None
        assert effects == []
        assert deps.ledger.conn.execute('SELECT count(*) FROM stage_journal').fetchone()[0] == 0
        assert deps.ledger.conn.execute('SELECT count(*) FROM briefings').fetchone()[0] == 0
    finally:
        deps.ledger.close()


def release_scorecard(config=None):
    cohort = policy.evaluation_cohort(config or {'model_identity': MODEL_IDENTITY}, ['kids_school'])
    cases = [
        {"case_id": "live", "severity": "P1", "expected": ["a"], "surfaced": ["a"]},
        {"case_id": "p2", "severity": "P2", "expected": ["b"], "surfaced": ["b"]},
        {"case_id": "negative", "case_type": "negative", "source_negative_reviewed": True,
         "expected": [], "surfaced": []},
        *[{"case_id": f"cross{i}", "case_type": "cross_domain", "expected": [f"cross{i}"], "surfaced": [f"cross{i}"]}
          for i in range(2)],
        {"case_id": "goal", "case_type": "goal_correction", "before": 2, "after": 1,
         "expected": ["corrected"], "surfaced": ["corrected"]},
    ]
    for case in cases:
        case.update(domain="kids_school", signal_id=f"sig_{case['case_id']}",
                    consented_live=True, reviewed=True, cohort=cohort,
                    catchup_id='cu_synthetic', split='held_out',
                    obligation_ids=list(case['expected']), thread_ids=[case['case_id']],
                    as_of='2026-10-12T00:00:00Z', latest_evidence_at='2026-10-11T00:00:00Z',
                    corpus_id='synthetic-release-corpus')
    result = {"schema": "artha.release.v4", 'cohort': cohort,
        "cases": len(cases), "live_cases": len(cases), "owner_signed_at": "reviewed",
        "evaluations": cases,
        "bounds": {"kids_school": {"minimum_precision": 0.8, "minimum_recall": 1,
            "max_source_age_hours": 48,
            "source_precedence": {name:list(pred.authority) for name,pred in PACK.predicates.items()},
            "normal_workload": {"signals": 20},
            "maximum_review_minutes": 15, "maximum_p95_seconds": 180, "maximum_monthly_usd": 50}},
        "measurements": {"kids_school": {'cohort': cohort, "review_minutes": 5, "p95_seconds": 100, "monthly_usd": 10,
            "workload":{"signals":20}, "source_age_hours":{"school_email":1, "calendar":1}}}}
    result['corpus'] = {'schema':'artha.release-corpus.v1','held_out':True,'independently_labeled':True,
        'development_obligation_ids':['development-obligation'],'development_thread_ids':['development-thread'],
        'development_end_at':'2026-10-01T00:00:00Z','id':'synthetic-release-corpus','cohort':cohort}
    result['document_fields'] = {name:{'correct':100,'total':100} for name in ('dates','amounts','names')}
    result['document_fields']['false_confirmations'] = {'false':0,'total':200}
    result['document_fields'].update(cohort=cohort,corpus_id='synthetic-release-corpus')
    measurement = result['measurements']['kids_school']
    measurement.update(completed_runs=20,total_runs=20)
    measurement['workloads'] = {}
    for name in ('normal_cold','normal_warm','long_gap','provider_reset','retention_1y','retention_2y','digest_limited'):
        measurement['workloads'][name] = {'cohort':cohort,'runs':5,'passed':True,'p95_seconds':1,
            'reference_machine':'synthetic-reference','workload':{'signals':20},
            'fair_progress':True,'incomplete_interpretation_safe':True,'verified_alerts_preserved':True,
            'coverage_degradation_reported':True,'query_verified':True,'restore_verified':True,
            'plaintext_fallback':False,'fetch_skipped':True,'vault_skipped':True}
        if name == 'long_gap':
            measurement['workloads'][name]['workload'] = {'signals':200,'gap_days':30}
        elif name == 'provider_reset':
            measurement['workloads'][name]['workload'] = {'signals':200,'provider_reset':True}
        elif name in ('retention_1y','retention_2y'):
            measurement['workloads'][name]['workload'] = {'signals':3650,'retention_years':1 if name == 'retention_1y' else 2}
    return result


@pytest.fixture
def app(tmp_path):
    led = Ledger(tmp_path / "local/catchup_core/ledger.db", id_key=KEY, cipher=CIPHER).open()
    led.migrate()
    blobs = EncryptedBlobStore(led._path.parent / "blobs", CIPHER, KEY)
    ref = blobs.put("mail", "Registration closes 2026-10-14")
    sid, _ = led.upsert_signal(SignalInput("school_email", "family", "m1", "v1", NOW.isoformat(), "h1", ref))
    fid = led.put_fact(FactInput(domain="kids_school", predicate="exam_registration_deadline",
        value="2026-10-14", primary_subject_id="kid1", observed_at=NOW.isoformat(), evidence=((sid, 0, 30),)))
    led.upsert_alert("exam", domain="kids_school", primary_subject_id="kid1", severity="P1", fact_ids=[fid])
    deps = co.Deps(led, PACK, blobs, KEY, {}, lambda _: '{"complete":true,"candidates":[]}', ["kid1"], "owner", lambda: NOW)
    (tmp_path / "state").mkdir()
    yield tmp_path, deps, fid, sid
    led.close()


@pytest.mark.parametrize("kind", ["blobs", "documents", "inbox", "corpus", "unknown"])
def test_export_rejects_unknown_artifact_provenance_before_publication(app, kind):
    root, deps, _, _ = app
    payload = {
        "documents": {"subject": ""},
        "inbox": {"actor": "other", "id": "synthetic"},
        "corpus": {"case_id": "different", "signal_id": 1},
    }.get(kind, {})
    data = CIPHER.encrypt(json.dumps(payload).encode(), aad=b"synthetic")[0]
    before = deps.ledger.conn.total_changes
    with pytest.raises(PermissionDenied, match="provenance"):
        commands._authorize_export_artifact(deps, f"{kind}/synthetic.ae1", data)
    assert deps.ledger.conn.total_changes == before
    assert not (root / kind).exists()


@pytest.mark.parametrize("subject", [None, 1, "", "unrecognized"])
def test_document_export_requires_known_subject_provenance(app, subject):
    _, deps, _, _ = app
    deps.ledger._known_subjects = frozenset({"owner", "kid1"})
    data = CIPHER.encrypt(json.dumps({"subject": subject}).encode(), aad=b"synthetic")[0]
    with pytest.raises(PermissionDenied, match="unknown subject provenance"):
        commands._authorize_export_artifact(deps, "documents/synthetic.ae1", data)


@pytest.mark.parametrize("kind", ["documents", "inbox", "corpus"])
def test_export_uses_authenticated_provenance_and_refuses_missing_key(app, kind):
    _, deps, _, sid = app
    payload = {
        "documents": {"subject": "kid1"},
        "inbox": {"actor": "owner", "id": "synthetic"},
        "corpus": {"case_id": "synthetic", "signal_id": sid},
    }[kind]
    data = CIPHER.encrypt(json.dumps(payload).encode(), aad=b"synthetic")[0]
    commands._authorize_export_artifact(deps, f"{kind}/synthetic.ae1", data)
    deps.ledger._cipher = None
    with pytest.raises(PermissionDenied, match="encryption key"):
        commands._authorize_export_artifact(deps, f"{kind}/synthetic.ae1", data)


@pytest.mark.parametrize("identity", [None, "", "  ", 123, "x" * 513])
def test_extraction_cancellation_rejects_invalid_identity_without_mutation(app, identity):
    root, deps, _, _ = app
    with pytest.raises(ValueError, match="invalid Catch-up identity"):
        commands.execute(Namespace(command="cancel-extraction", catchup_id=identity), deps, root)
    assert deps.ledger.conn.execute("SELECT count(*) FROM resource_cancellations").fetchone()[0] == 0


def test_extraction_cancellation_is_idempotent_without_undoing_completed_effects(app):
    root, deps, _, _ = app
    args = Namespace(command="cancel-extraction", catchup_id="cu-synthetic")
    result = commands.execute(args, deps, root)
    assert result == {
        "catchup_id": "cu-synthetic", "extraction_cancelled": True,
        "uncertain_usage_released": False, "completed_effects_undone": False,
    }
    assert commands.execute(args, deps, root) == result
    assert deps.ledger.conn.execute("SELECT count(*) FROM resource_cancellations").fetchone()[0] == 1


def args(command, **kw):
    return Namespace(command=command, domain=None, subject=None, as_of=None, days=7, **kw)


def test_read_and_review_commands_share_the_same_facts(app):
    root, deps, fid, _ = app
    assert commands.execute(args("beliefs"), deps, root)["beliefs"][0]["fact_id"] == fid
    why = commands.execute(args("why", fact_id=fid), deps, root)
    assert why["evidence"][0]["quote"] == "Registration closes 2026-10-14"
    assert views.explain(deps.ledger, deps.blobs, "owner", fid, as_of="2000-01-01T00:00:00Z")["value"] is None
    preview = commands.execute(args("correct", fact_id=fid, value="2026-10-16", apply=None), deps, root)
    commands.execute(args("correct", fact_id=fid, value="2026-10-16", apply=preview["apply_version"]), deps, root)
    assert deps.ledger.get_fact(fid, "owner").evidence == ()
    health = commands.execute(args("health"), deps, root)
    assert health["integrity"] is True
    states = {c["id"]: c for c in health["capabilities"]}
    assert states["RA-01"]["state"] == "not configured" and states["RA-01"]["measured"] == "no"   # no config here
    assert states["RA-19"]["state"] == "disabled"                   # later-scope: built, not yet part of the pilot
    assert health["performance"]["verdict"] == "no finished runs recorded yet"
    assert health["performance"]["cost"]["estimate"].startswith("unavailable")
    assert commands.execute(args("history"), deps, root) == {"briefings": []}
    response = commands.execute(args("feedback", obligation="exam", response="useful", reason="helped"), deps, root)
    assert response["feedback_id"] and b"helped" not in deps.ledger._path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        commands.execute(args("unknown"), deps, root)


def test_canonical_goals_and_open_items_reach_catchup(app):
    root, deps, _, _ = app
    (root / "state/open_items.md").write_text("- id: OI-001\n  status: open\n  description: Register exam\n")
    (root / "state/goals.md").write_text('---\ngoals:\n- id: G-1\n  title: Learn\n  status: active\n---\n')
    assert commands.execute(args("items"), deps, root)["items"][0]["id"] == "OI-001"
    assert commands.execute(args("goals"), deps, root)["goals"][0]["id"] == "G-1"
    incumbent = LegacyStateAdapter(root / "state", ["kids_school"], NOW).run([], budget_seconds=1)
    assert "Register exam" in incumbent.conclusions[0].text
    lines = runtime._goal_lines(root / "state").lines
    assert "unknown" in lines[0] and "not re-verified" in lines[0]
    request = CatchupRequest("goals", "owner", "cli", mode=ExecutionMode.SHADOW)
    result = co.run_catchup(request, replace(deps, goal_lines=lambda: lines))
    assert "G-1" in result.briefing_text


def test_capture_is_encrypted_and_fetched_during_catchup(app):
    root, deps, _, _ = app
    capture = commands.execute(args("capture", text="Owner note 2026-10-17"), deps, root)
    path = deps.ledger._path.parent / "inbox" / f"{capture['captured']}.ae1"
    assert b"Owner note" not in path.read_bytes()
    got = runtime._capture_fetcher(deps.ledger._path.parent, CIPHER, "owner")()
    assert got.records[0].text == "Owner note 2026-10-17"
    with pytest.raises(runtime.RuntimeConfigError):
        runtime._capture_fetcher(deps.ledger._path.parent, CIPHER, "someone_else")()


def test_pause_activity_export_and_forget(app, monkeypatch):
    root, deps, _, _ = app
    actions_db = deps.ledger._path.parent.parent / "actions.db"
    monkeypatch.setattr("catchup_core.runtime.action_database_path", lambda _workspace: actions_db)
    assert commands.execute(args("activity"), deps, root) == {"activity": [], "actions_paused": False}
    with sqlite3.connect(actions_db) as db:
        db.execute("CREATE TABLE actions(id,action_type,domain,status,created_at)")
        db.execute("INSERT INTO actions VALUES('a','reminder_create','home','unknown','2026-10-12')")
    first = commands.execute(args("activity"), deps, root)["activity"][0]
    assert first["status"] == "unknown" and first["lifecycle"] == "UNKNOWN" and first["group"] == "unknown"
    assert commands.execute(args("pause"), deps, root)["actions_paused"]
    assert not commands.execute(args("resume"), deps, root)["actions_paused"]
    export = root / "export.ae1"
    assert commands.execute(args("export", path=str(export)), deps, root)["dispatchable"] is False
    deps.ledger._blob_deleter = deps.blobs.delete
    result = commands.execute(args("forget", connector="school_email", scope="family", upstream="m1"), deps, root)
    assert result["deleted_signals"] == 1 and not views.beliefs(deps.ledger, "owner")


def test_calendar_from_connector_survives_restart_and_cancellation(app):
    _, deps, _, sid = app
    raw = {"id": "e1", "source": "calendar", "summary": "School", "kind": "school",
        "start": "2026-10-14T09:00:00-07:00", "end": "2026-10-14T15:00:00-07:00"}
    got = adapters.fetch_via_handler("calendar", "family", lambda **_: iter([raw]), since="2026-10-01")
    event = got.records[0].calendar
    deps.ledger.put_calendar("calendar", "family", "e1", sid, event)
    assert deps.ledger.calendar_for("owner")[0].kind == "school"
    assert deps.ledger.calendar_for("unknown") == []
    cancelled = adapters.fetch_via_handler("calendar", "family",
        lambda **_: iter([{"id": "e1", "status": "cancelled"}]), since="2026-10-01")
    assert cancelled.cancelled == ("e1",)
    deps.ledger.cancel_calendar("calendar", "family", "e1")
    assert deps.ledger.calendar_for("owner") == []
    bad = dict(raw, end="2026-10-14T08:00:00-07:00")
    assert adapters.fetch_via_handler("calendar", "family", lambda **_: iter([bad]), since="x").blind_spots


def test_actual_calendar_conflict_is_composed_with_a_citation(app):
    _, deps, _, _ = app
    travel = CalendarEvent("travel", NOW.date().replace(day=12), NOW.date().replace(day=13), True, "travel")
    text = "Exam registration deadline 2026-10-14"
    def extract(prompt):
        return json.dumps({"complete": True, "candidates": [{"predicate": "exam_registration_deadline",
            "subject_id": "kid1", "value": "2026-10-14", "quote": text, "span": [0, len(text)]}]})
    fetchers = {"calendar": lambda: co.ConnectorFetch("calendar", "family",
        (co.ConnectorRecord("travel", "Travel", calendar=travel),)),
        "school_email": lambda: co.ConnectorFetch("school_email", "family", (co.ConnectorRecord("new", text),))}
    def model(prompt):
        return extract(prompt) if text in prompt else '{"complete":true,"candidates":[]}'
    result = co.run_catchup(CatchupRequest("conflict", "owner", "cli", mode=ExecutionMode.SHADOW),
                            replace(deps, fetchers=fetchers, extractor=model))
    assert "travel day" in result.briefing_text and "WORTH A LOOK" in result.briefing_text


def test_cli_inspection_closes_storage_and_keeps_errors_on_stderr(app, monkeypatch):
    root, deps, _, _ = app
    monkeypatch.setattr(runtime, "load_runtime_config", lambda _: Namespace(viewer="owner"))
    monkeypatch.setattr(runtime, "build_deps", lambda *_, **__: deps)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert cli.main(["beliefs", "--shadow", "--json"], out=stdout, err=stderr) == 0
    assert json.loads(stdout.getvalue())["beliefs"] and stderr.getvalue() == ""
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(runtime, "build_deps", lambda *_, **__: (_ for _ in ()).throw(ValueError("locked")))
    assert cli.main(["health"], out=stdout, err=stderr) == 20 and "locked" in stderr.getvalue()


def test_release_checks_digest_live_evidence_host_and_identity(tmp_path):
    from test_cutover_fence import build_preservation_checkpoint, synthetic_recovery_acceptance
    path = tmp_path / "score.json"
    card = release_scorecard()
    path.write_text(json.dumps(card))
    clearance = tmp_path / "clearance.json"
    clearance.write_text('{"passed":true,"owner_signed_at":"2026-10-12"}')
    rollback = build_preservation_checkpoint(tmp_path, CIPHER)
    copy_path = tmp_path / "independent_copy.ack"
    copy_path.write_bytes(rollback.read_bytes())
    def approved(file):
        return {"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    rb_record = approved(rollback)
    rehearsal = tmp_path / "rehearsal.json"
    rehearsal.write_text(json.dumps(synthetic_recovery_acceptance(
        tmp_path, rollback, CIPHER, cohort=card['cohort'])))
    release = {"authority_host": "mac", "domains": ["kids_school"], "owner_approved_at": "2026-10-12",
               "stage1_clearance": approved(clearance), "rollback_snapshot": rb_record,
               "independent_copy": {**approved(copy_path), "failure_domain": "cloud",
                                    "owner_verified_at": "2026-10-12T12:00:00Z",
                                    "recovered_sha256": rb_record["sha256"]},
               "recovery_rehearsal": approved(rehearsal),
               "scorecard": approved(path)}
    request = CatchupRequest("c", "owner", "cli", host="mac")
    policy.validate_release(request, {"cutover": True, "release": release, 'model_identity': MODEL_IDENTITY},
                            viewer="owner", domains=["kids_school"], hostname="mac")
    for changed in (replace(request, actor="other"), replace(request, channel="telegram"),
                    replace(request, host="windows"), replace(request, pilot_domains=("health",))):
        with pytest.raises(PermissionDenied):
            policy.validate_release(changed, {"cutover": True, "release": release}, viewer="owner",
                                    domains=changed.pilot_domains or ["kids_school"], hostname="mac")
    path.write_text('{}')
    with pytest.raises(PermissionDenied, match="differs"):
        policy.validate_release(request, {"cutover": True, "release": release},
                                viewer="owner", domains=["kids_school"], hostname="mac")


@pytest.mark.parametrize("change", ["missing_field", "missing_path", "missing_file", "clearance_failed",
    "plaintext_rollback", "selective_rollback", "empty_cases", "low_precision", "no_signature", "bad_json",
    "missing_evaluations"])
def test_release_rejects_unreviewed_or_failed_evidence(tmp_path, change):
    import struct
    def artifact(name, value):
        path = tmp_path / name
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        path.write_bytes(data)
        return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}
    evaluation = release_scorecard()
    clearance = {"passed": True, "owner_signed_at": "reviewed"}
    if change == "clearance_failed":
        clearance["passed"] = False
    if change == "empty_cases":
        evaluation.update(cases=0, evaluations=[])
    if change == "low_precision":
        evaluation["evaluations"][0]["surfaced"] = ["a", "b", "c"]
    if change == "no_signature":
        evaluation.pop("owner_signed_at")
    if change == "missing_evaluations":
        evaluation.pop("evaluations")
    ack_bytes = b"ACK2" + struct.pack(">H", 12) + b"ck_synthetic" + struct.pack(">I", 32) + b"ES1" + b"0" * 29
    rb_bytes = b"plaintext" if change == "plaintext_rollback" else (
        b"AE1-selective" if change == "selective_rollback" else ack_bytes
    )
    rb_record = artifact("rollback", rb_bytes)
    copy_record = artifact("independent_copy", rb_bytes)
    rehearsal_record = artifact(
        "recovery_rehearsal",
        {"passed": True, "owner_signed_at": "reviewed", "reconciled": True, "checkpoint_sha256": rb_record["sha256"]},
    )
    release = {"authority_host": "mac", "domains": ["kids_school"], "owner_approved_at": "reviewed",
        "stage1_clearance": artifact("clearance", clearance),
        "rollback_snapshot": rb_record,
        "independent_copy": copy_record,
        "recovery_rehearsal": rehearsal_record,
        "scorecard": artifact("scorecard", b"not json" if change == "bad_json" else evaluation)}
    if change == "missing_field":
        release.pop("owner_approved_at")
    if change == "missing_path":
        release["scorecard"] = {}
    if change == "missing_file":
        (tmp_path / "scorecard").unlink()
    with pytest.raises(PermissionDenied):
        policy.validate_release(CatchupRequest("c", "owner", "cli", host="mac"), {"cutover": True, "release": release},
                                 viewer="owner", domains=["kids_school"], hostname="mac")


def test_ambiguous_household_subjects_are_never_assigned_by_list_order():
    config = runtime.RuntimeConfig({"school": {"subjects": ["kid1", "kid2"]}}, ["kid1", "kid2"], "owner",
                                    policy={"subject_aliases": {"kid1": ["Synthetic Alex"]}})
    assert runtime._subjects_for(config, "school", "for Synthetic Alex") == ["kid1"]
    assert runtime._subjects_for(config, "school", "for kid1 and kid2") == []
    assert runtime._subjects_for(config, "school", "for an unnamed student") == []
    scoped = runtime.RuntimeConfig({"school": {"subjects": ["kid2"]}}, ["kid1", "kid2"], "owner")
    assert runtime._subjects_for(scoped, "school", "generic school notice") == ["kid2"]


@pytest.mark.parametrize("problem", ["version", "bounds", "missing_domain", "no_live", "precision", "latency"])
def test_global_green_score_cannot_hide_an_unmeasured_domain(problem):
    result = release_scorecard()
    if problem == "version":
        result.pop("schema")
    elif problem == "bounds":
        result["bounds"]["kids_school"].pop("normal_workload")
    elif problem == "missing_domain":
        result["evaluations"][0]["domain"] = "finance"
    elif problem == "no_live":
        for case in result["evaluations"]:
            case["consented_live"] = False
    elif problem == "precision":
        result["evaluations"][0]["surfaced"] = ["a", "extra", "noise"]
    elif problem == "latency":
        result["measurements"]["kids_school"]["p95_seconds"] = 200
    with pytest.raises(PermissionDenied):
        policy._validate_domains(result, ["kids_school"])


def test_forget_purges_historical_claims_but_preserves_a_user_correction(app):
    _, deps, fid, _ = app
    old = deps.ledger.get_fact(fid, "owner")
    views.correct(deps.ledger, "owner", fid, "2026-10-16", old.recorded_at)
    deps.ledger.forget("school_email", "family", "m1")
    current = deps.ledger.get_fact(fid, "owner")
    assert current.value == "2026-10-16"
    history = deps.ledger.fact_history(fid, "owner")
    assert len(history) == 1 and history[0]["provenance_class"] == "user_confirmed"


def test_forget_retries_failed_archive_deletion(app):
    root, deps, fid, _ = app
    archive = deps.ledger._path.parent / "briefings/2026-10-12-archive.v1.ae1"
    archive.parent.mkdir()
    archive.write_bytes(b"encrypted private archive")
    deps.ledger.publish_briefing("archive", "text")
    deps.ledger.conn.execute("INSERT INTO briefing_facts VALUES('archive',?)", (fid,))
    def disk_full(_):
        raise OSError("disk unavailable")
    deps.ledger._artifact_deleter = disk_full
    with pytest.raises(OSError):
        deps.ledger.forget("school_email", "family", "m1")
    deps.ledger._artifact_deleter = lambda cids: runtime._purge_archives(deps.ledger._path.parent, deps.ledger, cids)
    deps.ledger.resume_pending_forgets()
    assert not archive.exists()
    assert commands.execute(args("history"), deps, root)["briefings"][0]["status"] == "superseded"


def test_read_inspection_uses_storage_guard_without_scheduler_dependency(app, monkeypatch):
    _, deps, _, _ = app
    calls = []
    deps.preflight = lambda: (_ for _ in ()).throw(RuntimeError('scheduler inventory unavailable'))
    deps.inspection_preflight = lambda: calls.append('storage verified')
    monkeypatch.setattr(runtime, 'load_runtime_config', lambda _: Namespace(viewer='owner'))
    monkeypatch.setattr(runtime, 'build_deps', lambda *a, **kw: deps)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert cli.main(['beliefs', '--shadow', '--json'], out=stdout, err=stderr) == 0
    assert calls == ['storage verified'] and json.loads(stdout.getvalue())['beliefs']


def test_mail_naming_a_withheld_adult_child_is_not_extracted_even_from_a_single_person_source():
    config = runtime.RuntimeConfig({"school": {"subjects": ["minor"]}}, ["owner", "minor", "adult"], "owner",
                                    policy={"withheld_names": ["Synthetic Sam", "Sam"]})
    assert runtime._subjects_for(config, "school", "Regarding: SYNTHETIC SAM, low score") == []
    assert runtime._subjects_for(config, "school", "Sam was absent today") == []
    assert runtime._subjects_for(config, "school", "Samuel's bus route") == ["minor"]      # whole words only
    assert runtime._subjects_for(config, "school", "generic school notice") == ["minor"]


def test_household_marks_an_adult_child_withheld_and_never_the_spouse():
    from datetime import date  # noqa: PLC0415

    from catchup_core import ids  # noqa: PLC0415
    from catchup_core.household import members  # noqa: PLC0415
    profile = {"family": {"primary_user": {"name": "Pat Doe"}, "spouse": {"name": "Kim Doe"},
                          "children": [{"name": "Sam Doe", "dob": "2008-01-01"}, {"name": "Lee Doe", "dob": "2014-01-01"}]}}
    people = members(profile, ids.HmacKey(1, b"k" * 32), "owner", date(2026, 10, 21))
    flags = {p.name: (p.consent_required, p.child) for p in people}
    assert flags == {"Pat Doe": (False, False), "Kim Doe": (True, False), "Sam Doe": (True, True),
                     "Lee Doe": (False, True)}
