"""Regression cases from the runtime audit, using isolated synthetic evidence."""

from __future__ import annotations

from datetime import timedelta

from catchup_core import coordinator as co
from catchup_core.bundles import ProducerKey, export_fetch, import_bundles
from catchup_core.contracts import CoverageStatus, RunStatus
from catchup_core.triage import CalendarEvent
from catchup_core import ledger as lg
from test_coordinator import KEY, CIPHER
import pytest
import json
from test_coordinator import NOW, email, req
import test_coordinator


@pytest.fixture
def world(tmp_path):
    yield from test_coordinator.world.__wrapped__(tmp_path)


def test_storage_failure_does_not_advance_fetch_watermark(world):
    led, blobs, make, _ = world
    original = blobs.put

    def fail_second(ref, text):
        if text == "second":
            raise OSError("synthetic full disk")
        return original(ref, text)

    blobs.put = fail_second
    got = co.run_catchup(
        req(),
        make({"school_email": email(records=(co.ConnectorRecord("a", "first"), co.ConnectorRecord("b", "second")))}),
    )
    assert got.status is RunStatus.BLOCKED
    assert not any(r["completed_at"] for r in led.coverage_rows())


def test_bundle_replays_until_consumer_commit_and_preserves_calendar(tmp_path):
    key = ProducerKey("secondary", b"s" * 32)
    event = CalendarEvent("e", NOW, NOW + timedelta(hours=1), False, "school", "")
    fetch = co.ConnectorFetch(
        "calendar", "family", (co.ConnectorRecord("e", "school", calendar=event),), cancelled=("cancelled",)
    )
    export_fetch(
        tmp_path,
        key,
        fetch,
        namespace="personal",
        recipient="authority",
        authority_generation=1,
        since=(NOW - timedelta(days=1)).isoformat(),
        now=NOW,
    )

    def load():
        return import_bundles(
            tmp_path,
            {key.producer: key},
            name="calendar",
            namespace="personal",
            recipient="authority",
            account_scope="family",
            authority_generation=1,
            now=NOW,
        )

    first = load()
    assert len(load().fetch.records) == 1
    assert first.fetch.records[0].calendar.start == NOW
    assert first.fetch.cancelled == ("cancelled",)
    first.fetch.commit()
    assert load().fetch.records == ()
    assert load().fetch.status is CoverageStatus.COMPLETE


def test_unknown_legacy_person_is_denied_even_to_owner(tmp_path):
    with lg.Ledger(
        tmp_path / "known.db", id_key=KEY, cipher=CIPHER, known_subjects=frozenset({"owner", "minor"})
    ).open() as led:
        led.migrate()
        fid = led.put_fact(
            lg.FactInput(
                domain="health",
                predicate="status",
                value="private",
                primary_subject_id="old_identity",
                observed_at=NOW.isoformat(),
            )
        )
        with pytest.raises(lg.PermissionDenied):
            led.get_fact(fid, "owner")


def test_named_obligation_conflicts_across_messages_instead_of_duplicate_alerts(world):
    led, _b, make, _p = world

    def extract(prompt):
        text = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        value = "2026-10-14" if "2026-10-14" in text else "2026-10-16"
        return json.dumps(
            {
                "complete": True,
                "candidates": [
                    {
                        "predicate": "exam_registration_deadline",
                        "subject_id": "kid1",
                        "value": value,
                        "span": [text.index(value), text.index(value) + 10],
                        "quote": value,
                        "obligation_ref": "SAT-registration",
                    }
                ],
            }
        )

    from test_coordinator import cal

    result = co.run_catchup(
        req(),
        make(
            {
                "school_email": email(
                    records=(
                        co.ConnectorRecord("a", "SAT registration 2026-10-14"),
                        co.ConnectorRecord("b", "SAT registration 2026-10-16"),
                    )
                ),
                "calendar": cal(),
            },
            extractor=extract,
        ),
    )
    assert result.status is RunStatus.DEGRADED
    assert all(f.status == "disputed" for f in led.facts_matching("kids_school", "exam_registration_deadline", "kid1"))


def test_goal_metric_is_persisted_and_recomputed_from_current_facts(world):
    from catchup_core.goals import observe_goals

    led, _b, make, _p = world
    co.run_catchup(req(), make({"school_email": email()}))
    authored = [
        {
            "id": "g1",
            "title": "Keep deadlines",
            "metric": {
                "kind": "on_time_obligations",
                "predicate": "exam_registration_deadline",
                "target": 1,
                "domain": "kids_school",
            },
        }
    ]
    lines = observe_goals(led, authored, "ved", NOW, required_sources=("school_email", "calendar"))
    assert "Keep deadlines" in lines[0] and "unknown" in lines[0]  # calendar missing: incomplete coverage
    assert led.conn.execute("SELECT 1 FROM goal_observations WHERE goal_id='g1'").fetchone()


def test_mixed_signal_reaches_incumbent_once_with_pilot_alert(world, tmp_path):
    from catchup_core.incumbent import GuardedIncumbent
    from catchup_core.legacy_state import LegacyStateAdapter
    from test_coordinator import EXTRACT, cal, MSG

    led, _b, make, published = world
    quote = "Home inspection needs booking."

    def extract(prompt):
        value = json.loads(EXTRACT(prompt))
        text = prompt.rsplit("<untrusted>\n", 1)[1].rsplit("\n</untrusted>", 1)[0]
        value["other_obligations"] = [
            {
                "domain": "home",
                "subject_id": "kid1",
                "quote": quote,
                "span": [text.index(quote), text.index(quote) + len(quote)],
            }
        ]
        return json.dumps(value)

    deps = make(
        {"school_email": email(records=(co.ConnectorRecord("mixed", MSG + " " + quote),)), "calendar": cal()},
        extractor=extract,
        incumbent=GuardedIncumbent(LegacyStateAdapter(tmp_path, ["kids_school"], NOW)),
    )
    got = co.run_catchup(req(), deps)
    assert got.artifact_ids and len(published) == 1
    assert got.briefing_text.count(quote) == 1
    assert "OTHER COMMITMENTS" in got.briefing_text


def test_capabilities_do_not_infer_action_success_from_finalization(world):
    from catchup_core.capabilities import runtime_states
    from pathlib import Path
    from test_coordinator import cal

    led, _b, make, _p = world
    co.run_catchup(req(), make({"school_email": email(records=()), "calendar": cal()}))
    states = {
        s["id"]: s
        for s in runtime_states(
            Path(__file__).resolve().parents[2], {"domains": ["kids_school"]}, led.conn, True, extractor_configured=True
        )
    }
    assert states["RA-01"]["state"] == "last succeeded"
    assert states["RA-05"]["state"] == "unknown" and states["RA-05"]["measured"] == "no"


def test_snapshot_refuses_concurrent_curated_edit(world, tmp_path):
    from catchup_core.backup import encrypted_snapshot

    led, _b, _make, _p = world
    target = tmp_path / "snapshot.ae1"
    with pytest.raises(lg.LedgerError, match="changed"):
        encrypted_snapshot(target, led._path, CIPHER, guard=lambda: False)
    assert not target.exists()


def test_old_briefing_gets_notice_without_rewriting_history(world):
    from test_coordinator import cal
    from catchup_core.views import correct

    led, _b, make, _p = world
    got = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}))
    fact = led.get_fact(got.artifact_ids[0], "ved")
    correct(led, "ved", fact.fact_id, "2026-10-16", fact.recorded_at)
    assert led.conn.execute("SELECT reason FROM correction_notices WHERE catchup_id=?", (req().catchup_id,)).fetchone()


def test_rollback_gate_decrypts_and_checks_the_actual_database(world, tmp_path):
    import hashlib
    from catchup_core.backup import encrypted_snapshot
    from catchup_core.policy import verify_rollback

    led, _b, _make, _p = world
    target = tmp_path / "rollback.ae1"
    encrypted_snapshot(target, led._path, CIPHER)

    def config():
        return {
            "release": {
                "rollback_snapshot": {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            }
        }

    verify_rollback(config(), CIPHER)
    target.write_bytes(b"AE1-invalid")
    with pytest.raises(lg.PermissionDenied, match="verified"):
        verify_rollback(config(), CIPHER)


def test_parental_consent_does_not_survive_adulthood(world):
    led, _b, _make, _p = world
    led.grant_consent("minor", "ved", "health", ["view"], "ved")
    assert led.consent_active("minor", "view", "ved", scope="health")
    led._consent_subjects = frozenset({"minor"})
    assert not led.consent_active("minor", "view", "ved", scope="health")


def test_household_ids_survive_profile_correction_and_key_rotation(tmp_path):
    from catchup_core import runtime as rt, ids
    import yaml

    (tmp_path / "config").mkdir()
    path = tmp_path / "config/user_profile.yaml"
    path.write_text("family:\n  primary_user: {name: Owner}\n  spouse: {name: Partner}\n")
    cfg = rt.RuntimeConfig({}, ["owner"], "owner")
    rt._bind_household(cfg, tmp_path, KEY, NOW.date())
    initial = cfg.subjects
    profile = yaml.safe_load(path.read_text())
    profile["family"]["spouse"]["name"] = "Corrected Partner"
    path.write_text(yaml.safe_dump(profile))
    rt._bind_household(cfg, tmp_path, ids.HmacKey(2, b"z" * 32), NOW.date())
    assert cfg.subjects == initial


def test_snapshot_retention_expires_old_copies_but_keeps_current(tmp_path):
    import os
    from catchup_core.runtime import _prune_snapshots

    a = tmp_path / "cu_old.ae1"
    b = tmp_path / "cu_current.ae1"
    a.write_bytes(b"old")
    b.write_bytes(b"current")
    os.utime(a, (0, 0))
    _prune_snapshots(tmp_path, NOW, 30)
    assert not a.exists() and b.exists()
    with pytest.raises(RuntimeError):
        _prune_snapshots(tmp_path, NOW, 0)


def test_feedback_review_uses_saved_inputs_and_class_correctness(world, tmp_path):
    from catchup_core import views
    from test_coordinator import cal

    led, blobs, make, _p = world
    co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}))
    obligation = led.conn.execute("SELECT obligation_key FROM alerts").fetchone()[0]
    fid = views.feedback(led, "ved", obligation, "dismiss", "Already planned", NOW)
    directory = tmp_path / "replay"
    views.replay_case(led, blobs, "ved", obligation, "dismiss", "Already planned", fid, NOW, directory)
    assert views.review_feedback(led, "ved", directory, None, None, NOW)[0]["status"] == "unreviewed"
    assert views.class_precision(led) == []
    views.review_feedback(led, "ved", directory, fid, "correct", NOW)
    assert views.class_precision(led)[0]["review_status"] == "correct"
    assert views.review_feedback(led, "ved", directory, None, None, NOW) == []
    with pytest.raises(ValueError):
        views.review_feedback(led, "ved", directory, None, "correct", NOW)
    with pytest.raises(ValueError):
        views.review_feedback(led, "ved", directory, "missing", None, NOW)
    next_id = views.feedback(led, "ved", obligation, "useful", "Helped", NOW)
    with pytest.raises(ValueError, match="bundle"):
        views.review_feedback(led, "ved", directory, next_id, "correct", NOW)


def test_missing_completion_attestation_cannot_be_quiet(world):
    from test_coordinator import cal

    _led, _blobs, make, _published = world
    deps = make({"school_email": email(), "calendar": cal()}, extractor=lambda _: json.dumps(dict(candidates=[])))
    got = co.run_catchup(req(), deps)
    assert got.status is RunStatus.DEGRADED and "Nothing urgent" not in got.briefing_text


def test_feedback_statistics_disappear_when_access_is_revoked(world):
    from catchup_core import views

    led, _blobs, _make, _published = world
    led._consent_subjects = frozenset({"adult"})
    grant = led.grant_consent("adult", "adult", "health", ["view"], "ved")
    fact = led.put_fact(
        lg.FactInput(
            domain="health",
            predicate="status",
            value="private",
            primary_subject_id="adult",
            observed_at=NOW.isoformat(),
        )
    )
    led.upsert_alert("private", domain="health", primary_subject_id="adult", severity="P1", fact_ids=[fact])
    fid = views.feedback(led, "ved", "private", "useful", "helped", NOW)
    led.conn.execute("UPDATE feedback SET review_status='correct' WHERE feedback_id=?", (fid,))
    assert views.class_precision(led) and views.precision(led)
    led.revoke_consent(grant)
    assert views.class_precision(led) == [] and views.precision(led) == []


@pytest.mark.parametrize("changed", ["precedence", "workload", "freshness"])
def test_release_bounds_match_runtime_and_measured_inputs(changed):
    from catchup_core.policy import _validate_domains
    from test_integrated_surfaces import release_scorecard

    data = release_scorecard()
    if changed == "precedence":
        data["bounds"]["kids_school"]["source_precedence"] = ["incorrect_source"]
    elif changed == "workload":
        data["measurements"]["kids_school"]["workload"] = {"signals": 1}
    else:
        data["measurements"]["kids_school"]["source_age_hours"]["calendar"] = 1000
    with pytest.raises(lg.PermissionDenied):
        _validate_domains(data, ["kids_school"])
