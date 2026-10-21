from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from catchup_core import crypto, ids
from catchup_core import domain_pack as dp
from catchup_core import ledger as lg
from catchup_core.contracts import FactStatus, Provenance, Severity
from catchup_core.triage import CalendarEvent, next_check_by, triage

KEY = ids.HmacKey(1, b"i" * 32)
CIPHER = crypto.EnvelopeCipher(crypto.StaticKeyProvider({1: b"\x07" * 32}))
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)      # Monday 09:00 Los Angeles
EXAM = "exam_registration_deadline"


def build_pack(holidays=()):
    import yaml
    data = yaml.safe_load((Path(dp.__file__).parent / "packs" / "kids_school.yaml").read_text())
    data["bindings"]["school_calendar"] = {"holidays": list(holidays)}
    return dp.load_pack(data)


@pytest.fixture
def env(tmp_path):
    ledger = lg.Ledger(tmp_path / "l.db", id_key=KEY, cipher=CIPHER, owner_id="ved").open()
    ledger.migrate()
    yield ledger
    ledger.close()


def put(ledger, predicate, value, subject="kid1", status=FactStatus.CURRENT, prov=Provenance.VERIFIED_SOURCE,
        connector="school_email"):
    sid, _ = ledger.upsert_signal(lg.SignalInput(connector, "acct", f"u-{predicate}-{value}-{subject}", "v1", "t",
                                                 f"h-{predicate}-{value}-{subject}", "b"))
    return ledger.put_fact(lg.FactInput(
        domain="kids_school", predicate=predicate, value=value, primary_subject_id=subject, observed_at="t",
        status=status, provenance=prov, evidence=((sid, 0, 4),))), sid


def fresh(hours=1):
    return {"school_email": NOW - timedelta(hours=hours), "calendar": NOW - timedelta(hours=hours)}


def conn_of(ledger):
    def _c(sid):
        return ledger.conn.execute("SELECT connector FROM signals WHERE signal_id=?", (sid,)).fetchone()[0]
    return _c


def run(ledger, pack=None, **kw):
    pack = pack or build_pack()
    kw.setdefault("freshness", fresh())
    return triage(ledger, pack, NOW, viewer="ved", connector_of=conn_of(ledger), **kw)


def test_deadline_in_horizon_surfaces_with_severity_and_lead_time(env):
    put(env, EXAM, "2026-10-14")                      # two days away -> P1
    r = run(env)
    a = r.alerts[0]
    assert a.severity is Severity.P1 and a.days_until == 2 and a.state == "actionable"
    assert a.latest_useful_action == date(2026, 10, 12)      # due minus 2 school days
    assert r.horizon[0].obligation_key == a.obligation_key
    assert r.next_check_by == date(2026, 10, 10) and r.check_now is True


def test_distant_deadline_is_listed_in_horizon_without_alert(env):
    put(env, EXAM, "2026-10-30")
    r = run(env)
    assert r.alerts == () and r.horizon == ()       # 18 days out, beyond the 14-day horizon
    put(env, "signup_deadline", "2026-10-20", subject="kid2")
    r = run(env)
    assert [h.predicate for h in r.horizon] == ["signup_deadline"]
    assert r.alerts == ()                            # no rule for signup deadlines in the pack


def test_stale_source_gives_unknown_not_reassurance(env):
    put(env, EXAM, "2026-10-14")
    r = run(env, freshness={"school_email": NOW - timedelta(hours=100)})
    a = r.alerts[0]
    assert a.state == "unknown_stale" and "100h ago" in a.uncertainty and "48h" in a.uncertainty
    assert a.severity is Severity.P1                  # the deadline is still shown, with its uncertainty


def test_never_verified_source_is_stale(env):
    put(env, EXAM, "2026-10-14")
    r = run(env, freshness={})
    assert r.alerts[0].state == "unknown_stale" and "never" in r.alerts[0].uncertainty


def test_only_current_strong_corroboration_refreshes_a_fact(env):
    fid, original = put(env, EXAM, "2026-10-14")
    other, _ = env.upsert_signal(lg.SignalInput("school_email", "other-account", "confirmation", "v1",
                                               "t", "confirmation-hash", "b"))
    ages = {original: NOW - timedelta(hours=100), other: NOW}
    assert run(env, freshness=ages).alerts[0].state == "unknown_stale"
    env.add_evidence(fid, other, 0, 4)
    weak = lambda sid: "school_email" if sid == original else "parent_note"
    assert run(env, freshness=ages, source_class_of=weak).alerts[0].state == "unknown_stale"
    assert run(env, freshness=ages, source_class_of=lambda _: "school_email").alerts[0].state == "actionable"


def test_disputed_fact_is_surfaced_as_dispute(env):
    put(env, EXAM, "2026-10-14", status=FactStatus.DISPUTED, prov=Provenance.DISPUTED)
    a = run(env).alerts[0]
    assert a.state == "disputed" and a.severity is None and "disagree" in a.uncertainty


def test_legacy_claim_is_labelled(env):
    put(env, EXAM, {"value": "2026-10-14", "lineage": "legacy_state:state/kids.md@abc"},
        prov=Provenance.LEGACY_UNVERIFIED)
    a = run(env).alerts[0]
    assert a.state == "legacy" and "not re-verified" in a.uncertainty and a.severity is Severity.P1


def test_overdue_outstanding_is_p1_and_resolved_is_quiet(env):
    put(env, EXAM, "2026-10-05")                      # a week ago
    key = "kids_school:kid1:exam_registration_deadline"
    r = run(env, obligation_states={key: "outstanding"})
    assert r.alerts[0].severity is Severity.P1 and r.horizon[0].days_until == -7
    r = run(env, obligation_states={key: "resolved"})
    assert r.alerts == ()
    r = run(env)  # no resolution evidence means an existing obligation remains outstanding
    assert r.alerts[0].severity is Severity.P1


def test_ranking_severity_then_deadline_then_key(env):
    put(env, EXAM, "2026-10-17", subject="kid2")      # P2 (5 days)
    put(env, EXAM, "2026-10-13", subject="kid1")      # P1
    put(env, "grade_alert", "below_threshold", subject="kid1")   # enum, not a date: no alert via date path
    r = run(env)
    assert [a.subject_id for a in r.alerts] == ["kid1", "kid2", "kid1"]
    assert [a.severity for a in r.alerts] == [Severity.P1, Severity.P2, Severity.P3]


def test_goal_relevance_breaks_ties_only(env):
    put(env, EXAM, "2026-10-14", subject="kid1")
    put(env, EXAM, "2026-10-14", subject="kid2")
    kid2 = "kids_school:kid2:exam_registration_deadline"
    r = run(env, goal_relevance={kid2: 0.9})
    assert r.alerts[0].subject_id == "kid2"


def test_next_check_by_helper():
    mk = lambda lua: type("A", (), {"latest_useful_action": lua})()   # noqa: E731
    assert next_check_by([], date(2026, 10, 12)) is None
    assert next_check_by([mk(date(2026, 10, 20)), mk(date(2026, 10, 15))], date(2026, 10, 12)) == date(2026, 10, 13)
    assert next_check_by([mk(date(2026, 10, 1))], date(2026, 10, 12)) == date(2026, 9, 29)


def test_deadline_vs_calendar_conflict_proposes_an_open_day(env):
    put(env, EXAM, "2026-10-20")                      # lead 2 school days -> Fri 2026-10-16
    travel = CalendarEvent("t1", date(2026, 10, 16), date(2026, 10, 17), all_day=True, kind="travel")
    r = run(env, calendar=[travel])
    f = r.cross_domain[0]
    assert f.case == "deadline_vs_calendar_conflict" and "travel" in f.detail
    assert f.suggested_date == date(2026, 10, 15)


def test_latest_useful_action_skips_school_holidays(env):
    put(env, EXAM, "2026-10-21")                      # Wed; lead 2 school days; Mon 19 is a holiday
    r = run(env, pack=build_pack(["2026-10-19"]))
    assert r.horizon[0].latest_useful_action == date(2026, 10, 16)
    assert r.cross_domain == ()                       # the arithmetic already avoids the holiday


def test_appointment_vs_school_schedule(env):
    put(env, "appointment_time", "2026-10-14T15:00:00-07:00", subject="kid2")
    from zoneinfo import ZoneInfo
    school = CalendarEvent("s1", datetime(2026, 10, 14, 8, tzinfo=ZoneInfo("America/Los_Angeles")),
                           datetime(2026, 10, 14, 16, tzinfo=ZoneInfo("America/Los_Angeles")), kind="school")
    r = run(env, calendar=[school])
    assert [f.case for f in r.cross_domain] == ["appointment_vs_school_schedule"]
    assert run(env, calendar=[]).cross_domain == ()


def test_unparsable_values_are_skipped_safely(env):
    put(env, EXAM, "sometime soon")
    put(env, "appointment_time", "later")
    r = run(env)
    assert [a.state for a in r.alerts] == ["no_deadline"]       # unknown stays visible, never guessed


def test_goal_relevance_is_a_tie_break_only():
    from catchup_core.triage import AlertDraft, _rank_key
    def alert(key, sev, domain="kids_school", lua=date(2026, 10, 14)):
        return AlertDraft(key, domain, "kid1", "p", "f", sev, lua, lua, 1, "actionable")
    p1_other = alert("b", Severity.P1, "finance")
    p2_goal = alert("a", Severity.P2)
    assert _rank_key(p1_other, {}, {"kids_school"}) < _rank_key(p2_goal, {}, {"kids_school"})      # severity wins
    early = alert("z", Severity.P1, "finance", date(2026, 10, 13))
    late_goal = alert("a", Severity.P1, "kids_school", date(2026, 10, 14))
    assert _rank_key(early, {}, {"kids_school"}) < _rank_key(late_goal, {}, {"kids_school"})        # date wins
    same_a, same_b = alert("a", Severity.P1, "finance"), alert("b", Severity.P1, "kids_school")
    assert _rank_key(same_b, {}, {"kids_school"}) < _rank_key(same_a, {}, {"kids_school"})          # tie: goal first
    assert _rank_key(same_a, {}, set()) < _rank_key(same_b, {}, set())                              # no goals: key order


def test_stale_enum_fact_is_unknown_not_reassurance(env):
    put(env, "accommodation_status", "approved", connector="school_email")
    old = {"school_email": NOW - timedelta(hours=500), "calendar": NOW - timedelta(hours=1)}
    result = run(env, freshness=old)
    stale = [a for a in result.alerts if a.predicate == "accommodation_status"]
    assert stale and stale[0].state == "unknown_stale" and "school_email last verified" in stale[0].uncertainty


def test_per_signal_freshness_overrides_the_connector_default(env):
    fid, sid = put(env, EXAM, "2026-10-20")
    stale_connector = {"school_email": NOW - timedelta(hours=500), "calendar": NOW}
    assert run(env, freshness=stale_connector).horizon[0].state == "unknown_stale"
    refreshed = {**stale_connector, sid: NOW - timedelta(hours=1)}     # this exact evidence was re-verified
    assert run(env, freshness=refreshed).horizon[0].state == "actionable"


def test_resolved_and_snoozed_alerts_stay_quiet_until_the_condition_changes(env):
    fid, sid = put(env, EXAM, "2026-10-14")
    key = f"kids_school:kid1:{EXAM}"
    env.upsert_alert(key, domain="kids_school", primary_subject_id="kid1", severity="P1",
                     deadline="2026-10-14", fact_ids=[fid])
    assert run(env).alerts                                              # open: surfaces
    with env.transaction() as db:
        db.execute("UPDATE alerts SET status='resolved' WHERE obligation_key=?", (key,))
    assert not run(env).alerts
    with env.transaction() as db:
        db.execute("UPDATE alerts SET status='snoozed', suppressed_until=? WHERE obligation_key=?",
                   ((NOW + timedelta(days=2)).isoformat(), key))
    assert not run(env).alerts
    with env.transaction() as db:
        db.execute("UPDATE alerts SET suppressed_until=? WHERE obligation_key=?",
                   ((NOW - timedelta(days=1)).isoformat(), key))
    assert run(env).alerts                                              # snooze expired: reopens
    with env.transaction() as db:
        db.execute("UPDATE alerts SET status='resolved', suppressed_until=NULL WHERE obligation_key=?", (key,))
    assert not run(env).alerts                                          # resolved again for the same evidence
    env.put_fact(lg.FactInput(domain="kids_school", predicate=EXAM, value="2026-10-15", primary_subject_id="kid1",
                              observed_at="t2", status=FactStatus.CURRENT, provenance=Provenance.VERIFIED_SOURCE,
                              evidence=((sid, 0, 4),)), fact_id=fid)
    assert run(env).alerts                                              # the deadline changed: resolution no longer applies
