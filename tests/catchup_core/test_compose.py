from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from catchup_core import compose as cp
from catchup_core.contracts import Severity
from catchup_core.triage import AlertDraft, CrossDomainFinding, TriageResult

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)


def alert(key="kids_school:kid1:exam", sev=Severity.P1, state="actionable", days=2, note="", pred="exam_registration_deadline"):
    return AlertDraft(key, "kids_school", "kid1", pred, "fct_1", sev, date(2026, 10, 14), date(2026, 10, 12),
                      days, state, note)


def result(alerts=(), horizon=None, cross=(), nxt=None, check_now=False, uncertain=()):
    return TriageResult(tuple(alerts), tuple(horizon if horizon is not None else alerts), tuple(cross), nxt,
                        check_now, tuple(uncertain))


EV = {"kids_school:kid1:exam": [("evidence:abc", "school email", "2026-10-11T08:00Z", "acct")]}
COV = "Coverage: all required sources checked."


def test_alert_briefing_has_one_thing_citation_and_evidence_table():
    b = cp.compose(NOW, result([alert()], nxt=date(2026, 10, 10), check_now=True),
                   evidence_for=EV, coverage_line=COV)
    assert "ONE THING" in b.text and "[P1]" in b.text and "[1]" in b.text
    assert "[1] school email, observed 2026-10-11T08:00Z, acct" in b.text
    assert "Next check: check now." in b.text
    assert b.one_thing is not None and not b.quiet
    assert cp.validate_briefing(b.text, b.evidence, has_gaps=False) == []


def test_quiet_only_when_everything_is_known():
    b = cp.compose(NOW, result(), evidence_for={}, coverage_line=COV)
    assert b.quiet and "Nothing urgent in the sources checked." in b.text
    assert cp.validate_briefing(b.text, b.evidence, has_gaps=False) == []


def test_gap_blocks_the_quiet_claim_and_names_it():
    b = cp.compose(NOW, result(), evidence_for={}, coverage_line=COV,
                   coverage_gaps=["google_calendar degraded (7 records rejected)"])
    assert not b.quiet
    assert "not an all-clear" in b.text and "google_calendar degraded" in b.text
    assert "Nothing urgent" not in b.text
    assert cp.validate_briefing(b.text, b.evidence, has_gaps=True) == []


def test_unresolved_signals_and_uncertain_inputs_block_quiet():
    b = cp.compose(NOW, result(uncertain=["k"]), evidence_for={}, coverage_line=COV, unresolved_signals=3)
    assert not b.quiet and "3 fetched item(s)" in b.text


def test_missing_evidence_is_declared_not_invented():
    b = cp.compose(NOW, result([alert()]), evidence_for={}, coverage_line=COV)
    assert "{{MISSING: no evidence recorded}}" in b.text and b.missing == ("kids_school:kid1:exam",)
    assert cp.validate_briefing(b.text, b.evidence, has_gaps=False) == []


def test_disputed_unknown_and_overdue_wording():
    d = cp.compose(NOW, result([alert(state="disputed", sev=None)]), evidence_for=EV, coverage_line=COV)
    assert "sources disagree" in d.text and "[unscored]" in d.text
    n = cp.compose(NOW, result([alert(state="no_deadline", sev=None)]), evidence_for=EV, coverage_line=COV)
    assert "no usable date; unknown" in n.text
    o = cp.compose(NOW, result([alert(days=-3)]), evidence_for=EV, coverage_line=COV)
    assert "3 days overdue" in o.text
    s = cp.compose(NOW, result([alert(days=1)]), evidence_for=EV, coverage_line=COV)
    assert "(1 day)" in s.text


def test_remaining_alerts_horizon_and_cross_domain_sections():
    a1 = alert("k:1", Severity.P1)
    a2 = alert("k:2", Severity.P2)
    h = alert("k:3", None, days=10)
    ev = {k: [("evidence:" + k, "portal", "2026-10-11", "")] for k in ("k:1", "k:2", "k:3")}
    f = CrossDomainFinding("deadline_vs_calendar_conflict", "k:1", "latest useful action falls on a travel day",
                           date(2026, 10, 15))
    b = cp.compose(NOW, result([a1, a2], horizon=[a1, a2, h], cross=[f], nxt=date(2026, 10, 13)),
                   evidence_for=ev, coverage_line=COV, goal_lines=["Registrations on time: 3 of 3"])
    assert "ALERTS" in b.text and "COMING UP" in b.text and "WORTH A LOOK" in b.text and "GOALS" in b.text
    assert "Consider Thu Oct 15." in b.text and "next check by Tue Oct 13" in b.text
    assert len(b.evidence) == 3


def test_sanitize_strips_links_images_markup_and_bounds_length():
    nasty = "see ![x](http://evil.example/p.png) and [click](http://evil.example) or http://evil.example/a <b>bold</b>"
    out = cp.sanitize(nasty)
    assert "http" not in out and "<" not in out and "![" not in out and "click" in out
    assert len(cp.sanitize("x" * 1000)) <= 160


def test_injected_text_in_uncertainty_cannot_render_a_link():
    a = alert(note="visit http://evil.example/steal?d=1 now")
    b = cp.compose(NOW, result([a]), evidence_for=EV, coverage_line=COV)
    assert "evil.example" not in b.text
    assert cp.validate_briefing(b.text, b.evidence, has_gaps=False) == []


@pytest.mark.parametrize("text,gaps,expect", [
    ("• [P1] kid: exam due [9]\n", False, "does not resolve"),
    ("• [P1] kid: exam due Tue\n", False, "without a citation"),
    ("Nothing urgent today.\n", True, "quiet-day language"),
    ("see http://x.example\n", False, "link, image or markup"),
    ("<img src=x>\n", False, "link, image or markup"),
])
def test_validator_catches_each_violation_class(text, gaps, expect):
    problems = cp.validate_briefing(text, [], has_gaps=gaps)
    assert any(expect in p for p in problems), problems


def test_evidence_table_dedupes_and_resolves():
    t = cp.EvidenceTable()
    assert t.add("e1", "a", "t") == 1 and t.add("e2", "b", "t") == 2 and t.add("e1", "a", "t") == 1
    assert len(t) == 2 and t.resolve(2).token == "e2" and t.resolve(7) is None


def test_goal_plans_show_in_standard_and_deep_but_not_in_short_formats():
    from datetime import datetime, timezone

    from catchup_core.contracts import BriefingFormat
    from catchup_core.triage import TriageResult
    empty = TriageResult((), (), (), None, False)
    now = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    kw = dict(evidence_for={}, coverage_line="Coverage: ok.", plan_lines=["G1: ready — next step: File"])
    assert "GOAL PLANS" in cp.compose(now, empty, **kw).text
    assert "GOAL PLANS" in cp.compose(now, empty, fmt=BriefingFormat.DEEP, **kw).text
    assert "GOAL PLANS" not in cp.compose(now, empty, fmt=BriefingFormat.FLASH, **kw).text
    assert "GOAL PLANS" not in cp.compose(now, empty, fmt=BriefingFormat.DIGEST, **kw).text


def test_responsibilities_show_in_standard_but_not_in_short_formats():
    from catchup_core.contracts import BriefingFormat
    from catchup_core.triage import TriageResult
    empty = TriageResult((), (), (), None, False)
    now = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    kw = dict(evidence_for={}, coverage_line="Coverage: ok.", responsibility_lines=["R1: check now — Renew"])
    assert "RESPONSIBILITIES" in cp.compose(now, empty, **kw).text
    assert "RESPONSIBILITIES" not in cp.compose(now, empty, fmt=BriefingFormat.DIGEST, **kw).text


def test_household_section_shows_in_standard_only():
    from catchup_core.contracts import BriefingFormat
    from catchup_core.triage import TriageResult
    empty = TriageResult((), (), (), None, False)
    now = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
    kw = dict(evidence_for={}, coverage_line="Coverage: ok.", coordination_lines=["H1: RSVP — waiting"])
    assert "HOUSEHOLD" in cp.compose(now, empty, **kw).text
    assert "HOUSEHOLD" not in cp.compose(now, empty, fmt=BriefingFormat.FLASH, **kw).text
