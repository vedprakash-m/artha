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
    r = run(env)                                       # unknown state: uncertain, not guessed
    assert r.alerts == () and key in r.uncertain


def test_ranking_severity_then_deadline_then_key(env):
    put(env, EXAM, "2026-10-17", subject="kid2")      # P2 (5 days)
    put(env, EXAM, "2026-10-13", subject="kid1")      # P1
    put(env, "grade_alert", "below_threshold", subject="kid1")   # enum, not a date: no alert via date path
    r = run(env)
    assert [a.subject_id for a in r.alerts] == ["kid1", "kid2"]
    assert [a.severity for a in r.alerts] == [Severity.P1, Severity.P2]


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
    assert next_check_by([mk(date(2026, 10, 1))], date(2026, 10, 12)) is None


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
    school = CalendarEvent("s1", datetime(2026, 10, 14, 8, tzinfo=timezone.utc),
                           datetime(2026, 10, 14, 15, tzinfo=timezone.utc), kind="school")
    r = run(env, calendar=[school])
    assert [f.case for f in r.cross_domain] == ["appointment_vs_school_schedule"]
    assert run(env, calendar=[]).cross_domain == ()


def test_unparsable_values_are_skipped_safely(env):
    put(env, EXAM, "sometime soon")
    put(env, "appointment_time", "later")
    r = run(env)
    assert [a.state for a in r.alerts] == ["no_deadline"]       # unknown stays visible, never guessed
