"""CAL-ATTR: events that name no single household person must not hide the calendar source (CA-01..CA-07)."""
from __future__ import annotations

import pytest

from catchup_core import coordinator as co
from catchup_core import runtime
from catchup_core.contracts import CoverageStatus, RunStatus

from test_coordinator import cal, email, req, world  # noqa: F401

CANDIDATES = ["kid1", "kid2"]


def config(**policy):
    return runtime.RuntimeConfig({"calendar": {"subjects": CANDIDATES}, "school": {"subjects": ["kid1"]}},
                                 ["owner", *CANDIDATES], "owner",
                                 policy={"subject_aliases": {"kid1": ["Synthetic Alex"]}, **policy})


def test_unnamed_event_records_all_candidates_for_access_only():
    assert runtime._access_subjects_for(config(), "calendar", "Synthetic Holiday") == CANDIDATES      # CA-02
    assert runtime._access_subjects_for(config(), "calendar", "kid1 and kid2 trip") == CANDIDATES    # CA-05
    assert runtime._subjects_for(config(), "calendar", "Synthetic Holiday") == []                     # CA-01


def test_named_event_and_single_subject_source_are_unchanged():
    assert runtime._access_subjects_for(config(), "calendar", "Synthetic Alex recital") == ["kid1"]
    assert runtime._access_subjects_for(config(), "school", "generic notice") == ["kid1"]             # CA-07


def test_withheld_name_gets_no_access_fallback():
    withheld = config(withheld_names=["Synthetic Sam"])
    assert runtime._access_subjects_for(withheld, "calendar", "Synthetic Sam dentist") == []          # CA-04


def attribution_deps(make, **extra):
    cfg = config()
    return make({"school_email": email(), "calendar": cal(records=[co.ConnectorRecord("e1", "Synthetic Holiday")])},
                subjects_for=lambda connector, text: runtime._subjects_for(cfg, connector, text),
                access_subjects_for=lambda connector, text: runtime._access_subjects_for(cfg, connector, text),
                scope_subjects={"school_email": CANDIDATES, "calendar": CANDIDATES}, **extra)


def test_unnamed_event_keeps_calendar_coverage_visible_but_is_still_withheld(world):                  # noqa: F811
    ledger, _blobs, make, _published = world
    result = co.run_catchup(req(), attribution_deps(make))
    assert result.status is RunStatus.DEGRADED                                                        # CA-01
    calendar = [c for c in result.coverage if c.connector == "calendar"]
    assert calendar and calendar[0].status is CoverageStatus.COMPLETE                                 # CA-02
    assert "1 calendar event(s) not attributed to a person" in result.briefing_text                   # CA-06
    assert "Calendar: not verified" not in result.briefing_text


def test_consent_required_candidate_keeps_the_source_hidden(world):                                   # noqa: F811
    ledger, _blobs, make, _published = world
    ledger._consent_subjects = frozenset({"kid2"})
    result = co.run_catchup(req(), attribution_deps(make))
    assert not [c for c in result.coverage if c.connector == "calendar" and c.status is CoverageStatus.COMPLETE]  # CA-03


@pytest.mark.parametrize("without", [True])
def test_old_deployments_without_the_new_dependency_behave_as_before(world, without):                 # noqa: F811
    ledger, _blobs, make, _published = world
    cfg = config()
    deps = make({"school_email": email(), "calendar": cal(records=[co.ConnectorRecord("e1", "Synthetic Holiday")])},
                subjects_for=lambda connector, text: runtime._subjects_for(cfg, connector, text),
                scope_subjects={"school_email": CANDIDATES, "calendar": CANDIDATES})
    result = co.run_catchup(req(), deps)
    assert not [c for c in result.coverage if c.connector == "calendar" and c.status is CoverageStatus.COMPLETE]
