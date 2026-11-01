"""REMINDERS (owner decisions 2 and 4): a reminder proposal for every future-dated obligation, never overdue (RM-01..RM-08)."""
from __future__ import annotations

from datetime import date

from catchup_core import coordinator as co
from catchup_core import reminders
from catchup_core.contracts import ExecutionMode, Severity
from catchup_core.triage import AlertDraft

from test_coordinator import cal, email, req, world  # noqa: F401

LABELS = {"kid1": "Synthetic Kid"}


def draft(key="k1", deadline=date(2026, 11, 4), days=4, state="actionable", **kw):
    fields = dict(obligation_key=key, domain="kids_school", subject_id="kid1", predicate="signup_deadline", fact_id="f1",
                  severity=Severity.P2, deadline=deadline, latest_useful_action=None, days_until=days, state=state)
    return AlertDraft(**{**fields, **kw})


class Recorder:
    def __init__(self, refuse=()):
        self.calls, self.refuse = [], set(refuse)

    def __call__(self, action_type, domain, title, parameters, **kw):
        if title in self.refuse:
            raise ValueError("duplicate")
        self.calls.append((action_type, domain, title, parameters, kw))
        return object()


def test_a_future_dated_obligation_becomes_one_reminder_proposal():                                 # RM-01
    rec = Recorder()
    result = reminders.propose([draft()], LABELS, rec)
    [(kind, domain, title, params, _kw)] = rec.calls
    assert (kind, domain) == ("reminder_create", "kids_school")
    assert "Synthetic Kid" in title and "signup deadline" in title and "2026-11-04" in title
    assert params["due_date"] == "2026-11-04" and params["title"] == title
    assert result.proposed == 1 and result.skipped == 0


def test_due_today_counts_as_future_dated_but_overdue_does_not():                                  # RM-02
    rec = Recorder()
    reminders.propose([draft("a", date(2026, 10, 31), 0), draft("b", date(2026, 10, 25), -6)], LABELS, rec)
    assert [c[3]["due_date"] for c in rec.calls] == ["2026-10-31"]


def test_undated_disputed_and_empty_alerts_get_no_reminder():                                      # RM-03
    rec = Recorder()
    reminders.propose([draft("a", None, None, state="no_deadline"), draft("b", state="disputed")], LABELS, rec)
    assert rec.calls == []


def test_an_unscored_stale_deadline_still_gets_a_reminder():                                       # RM-04
    rec = Recorder()
    reminders.propose([draft(severity=None, state="unknown_stale")], LABELS, rec)
    assert len(rec.calls) == 1


def test_an_obligation_seen_twice_is_proposed_once():                                               # RM-05
    rec = Recorder()
    assert reminders.propose([draft("k1"), draft("k1")], LABELS, rec).proposed == 1


def test_a_refused_proposal_is_counted_and_never_stops_the_rest():                                 # RM-06
    first = reminders.title_for(draft("a"), LABELS)
    rec = Recorder(refuse=[first])
    result = reminders.propose([draft("a"), draft("b", date(2026, 11, 9), 9)], LABELS, rec)
    assert (result.proposed, result.skipped) == (1, 1)


def test_the_title_names_the_obligation_not_the_key_or_a_free_text_quote():                        # RM-07
    title = reminders.title_for(draft(), LABELS)
    assert title == "Synthetic Kid: signup deadline due 2026-11-04 (kids_school)"
    assert "k1" not in title


def test_a_hundred_obligations_are_capped():                                                       # RM-08
    rec = Recorder()
    many = [draft(f"k{i}", date(2026, 12, 1), 31, predicate=f"p{i}") for i in range(reminders.MAX_PER_RUN + 25)]
    assert reminders.propose(many, LABELS, rec).proposed == reminders.MAX_PER_RUN


def test_an_authoritative_run_proposes(world):                                                     # noqa: F811  RM-09
    _ledger, _blobs, make, _published = world
    seen = []
    co.run_catchup(req(), make({"school_email": email(), "calendar": cal()},
                               reminder_proposer=lambda alerts: seen.append(alerts)))
    assert len(seen) == 1


def test_a_shadow_run_never_proposes(world):                                                       # noqa: F811  RM-10
    _ledger, _blobs, make, _published = world
    seen = []
    co.run_catchup(req(mode=ExecutionMode.SHADOW), make({"school_email": email(), "calendar": cal()},
                                                        reminder_proposer=lambda alerts: seen.append(alerts)))
    assert seen == []


def test_the_runtime_hook_proposes_through_the_executor_and_survives_its_failure():                # RM-11
    from catchup_core import runtime
    rec = Recorder()
    class Executor:
        propose = rec
    runtime.reminder_hook(lambda: Executor(), LABELS)([draft()])
    assert len(rec.calls) == 1

    def broken():
        raise RuntimeError("queue unavailable")
    runtime.reminder_hook(broken, LABELS)([draft()])           # must not raise: the briefing is already built
