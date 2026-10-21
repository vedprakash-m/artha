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
from catchup_core.contracts import CatchupRequest, ExecutionMode, FactStatus
from catchup_core.legacy_state import LegacyStateAdapter
from catchup_core.ledger import FactInput, Ledger, PermissionDenied, SignalInput
from catchup_core.triage import CalendarEvent
from test_user_surfaces import CIPHER, KEY, Keyring
from catchup_core.blobs import EncryptedBlobStore

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
PACK = dp.load_pack_file(Path(dp.__file__).parent / "packs/kids_school.yaml")


def release_scorecard():
    return {"schema": "artha.release.v1", "cases": 1, "live_cases": 1, "owner_signed_at": "reviewed",
        "evaluations": [{"case_id": "live", "domain": "kids_school", "consented_live": True,
                         "expected": ["a"], "surfaced": ["a"]}],
        "bounds": {"kids_school": {"minimum_precision": 0.8, "minimum_recall": 1,
            "max_source_age_hours": 48,
            "source_precedence": {name:list(pred.authority) for name,pred in PACK.predicates.items()},
            "normal_workload": {"signals": 20},
            "maximum_review_minutes": 15, "maximum_p95_seconds": 180, "maximum_monthly_usd": 50}},
        "measurements": {"kids_school": {"review_minutes": 5, "p95_seconds": 100, "monthly_usd": 10,
            "workload":{"signals":20}, "source_age_hours":{"school_email":1, "calendar":1}}}}


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
    assert states["RA-19"]["state"] == "not implemented"
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
    lines = runtime._goal_lines(root / "state")
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


def test_pause_activity_export_and_forget(app):
    root, deps, _, _ = app
    assert commands.execute(args("activity"), deps, root) == {"activity": [], "actions_paused": False}
    with sqlite3.connect(deps.ledger._path.parent.parent / "actions.db") as db:
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
    path = tmp_path / "score.json"
    path.write_text(json.dumps(release_scorecard()))
    clearance = tmp_path / "clearance.json"
    clearance.write_text('{"passed":true,"owner_signed_at":"2026-10-12"}')
    rollback = tmp_path / "rollback.ae1"
    rollback.write_bytes(b"AE1-synthetic-fixture")
    def approved(file):
        return {"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    release = {"authority_host": "mac", "domains": ["kids_school"], "owner_approved_at": "2026-10-12",
               "stage1_clearance": approved(clearance), "rollback_snapshot": approved(rollback),
               "scorecard": approved(path)}
    request = CatchupRequest("c", "owner", "cli", host="mac")
    policy.validate_release(request, {"release": release}, viewer="owner", domains=["kids_school"], hostname="mac")
    for changed in (replace(request, actor="other"), replace(request, channel="telegram"),
                    replace(request, host="windows"), replace(request, pilot_domains=("health",))):
        with pytest.raises(PermissionDenied):
            policy.validate_release(changed, {"release": release}, viewer="owner",
                                    domains=changed.pilot_domains or ["kids_school"], hostname="mac")
    path.write_text('{}')
    with pytest.raises(PermissionDenied, match="differs"):
        policy.validate_release(request, {"release": release}, viewer="owner", domains=["kids_school"], hostname="mac")


@pytest.mark.parametrize("change", ["missing_field", "missing_path", "missing_file", "clearance_failed",
    "plaintext_rollback", "empty_cases", "low_precision", "no_signature", "bad_json", "missing_evaluations"])
def test_release_rejects_unreviewed_or_failed_evidence(tmp_path, change):
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
    release = {"authority_host": "mac", "domains": ["kids_school"], "owner_approved_at": "reviewed",
        "stage1_clearance": artifact("clearance", clearance),
        "rollback_snapshot": artifact("rollback", b"plaintext" if change == "plaintext_rollback" else b"AE1"),
        "scorecard": artifact("scorecard", b"not json" if change == "bad_json" else evaluation)}
    if change == "missing_field":
        release.pop("owner_approved_at")
    if change == "missing_path":
        release["scorecard"] = {}
    if change == "missing_file":
        (tmp_path / "scorecard").unlink()
    with pytest.raises(PermissionDenied):
        policy.validate_release(CatchupRequest("c", "owner", "cli", host="mac"), {"release": release},
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
        result["evaluations"][0]["consented_live"] = False
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
