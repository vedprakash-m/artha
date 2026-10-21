"""Household identity comes from the authored profile; unknown age and spouses fail conservative (RA-12 v0)."""
from __future__ import annotations

from datetime import date

import pytest

from catchup_core import ids
from catchup_core.household import members

KEY = ids.HmacKey(1, b"h" * 32)
TODAY = date(2026, 10, 20)


def profile(**family):
    base = {"primary_user": {"name": "Pat Owner", "nickname": "Pat"},
            "spouse": {"name": "Sam Owner"},
            "children": [{"name": "Kid One", "dob": "2015-05-05"}, {"name": "Adult Child", "dob": "2005-01-01"}]}
    base.update(family)
    return {"family": base}


def test_adults_and_spouse_require_consent_minors_do_not():
    people = members(profile(), KEY, "owner", TODAY)
    by_name = {p.name: p for p in people}
    assert by_name["Pat Owner"].subject_id == "owner" and not by_name["Pat Owner"].consent_required
    assert by_name["Sam Owner"].consent_required                       # spouse
    assert by_name["Adult Child"].consent_required                     # DOB says 21
    assert not by_name["Kid One"].consent_required                     # DOB says 11


def test_birthday_not_yet_reached_keeps_a_child_under_parental_authority():
    kid = {"name": "Almost", "dob": "2008-12-31"}
    assert not {p.name: p for p in members(profile(children=[kid]), KEY, "owner", TODAY)}["Almost"].consent_required
    assert {p.name: p for p in members(profile(children=[kid]), KEY, "owner", date(2026, 12, 31))}["Almost"].consent_required


def test_unknown_or_garbled_dob_is_treated_as_adult():
    kid = {"name": "Mystery", "dob": "not-a-date"}
    assert {p.name: p for p in members(profile(children=[kid]), KEY, "owner", TODAY)}["Mystery"].consent_required


def test_aliases_use_first_name_and_nickname_and_ids_are_stable_keyed():
    first = members(profile(), KEY, "owner", TODAY)
    again = members(profile(), KEY, "owner", TODAY)
    assert [p.subject_id for p in first] == [p.subject_id for p in again]
    assert set(first[0].aliases) == {"Pat Owner", "Pat"}
    other = members(profile(), ids.HmacKey(2, b"z" * 32), "owner", TODAY)
    assert first[2].subject_id != other[2].subject_id                  # keyed: not derivable from the name


def test_disabled_spouse_and_missing_primary_user():
    people = members(profile(spouse={"name": "Sam", "enabled": False}), KEY, "owner", TODAY)
    assert "Sam" not in {p.name for p in people}
    with pytest.raises(ValueError):
        members({"family": {}}, KEY, "owner", TODAY)


def test_duplicate_identities_are_refused():
    twins = [{"name": "Twin", "dob": "2015-01-01"}, {"name": "Twin", "dob": "2015-01-01"}]
    with pytest.raises(ValueError):
        members(profile(children=twins), KEY, "owner", TODAY)
