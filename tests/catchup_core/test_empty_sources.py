"""GAP-EMPTY: a complete fetch that returned no mail must not read as a verified quiet Domain (ES-01..ES-03)."""
from __future__ import annotations

from catchup_core import coordinator as co

from test_coordinator import cal, email, req, world  # noqa: F401


def test_a_source_with_no_mail_is_named_in_the_coverage_line(world):                                  # noqa: F811
    _ledger, _blobs, make, _published = world
    result = co.run_catchup(req(), make({"school_email": email(records=()), "calendar": cal()}))
    assert "No mail seen from: school_email." in result.briefing_text                                # ES-01


def test_a_source_with_mail_is_not_named(world):                                                     # noqa: F811
    _ledger, _blobs, make, _published = world
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal()}))
    assert "No mail seen" not in result.briefing_text                                                # ES-02


def test_the_calendar_is_not_reported_as_missing_mail(world):                                        # noqa: F811
    _ledger, _blobs, make, _published = world
    result = co.run_catchup(req(), make({"school_email": email(), "calendar": cal(records=())}))
    assert "No mail seen" not in result.briefing_text                                                # ES-03
