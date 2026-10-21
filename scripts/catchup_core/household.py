"""Resolve household identity from the authored profile, without inferring consent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from catchup_core import ids


@dataclass(frozen=True)
class Person:
    subject_id: str
    name: str
    aliases: tuple[str, ...]
    consent_required: bool


def members(profile: dict[str, Any], key: ids.HmacKey, viewer: str, today: date) -> list[Person]:
    family = profile.get("family", {})
    owner = family.get("primary_user", {})
    if not isinstance(owner, dict) or not owner.get("name"):
        raise ValueError("household profile needs its primary user")
    output = [_person(owner, viewer, False)]
    spouse = family.get("spouse", {})
    others = ([spouse] if spouse.get("enabled", True) and spouse.get("name") else []) + family.get("children", [])
    for record in others:
        name, dob = str(record["name"]), str(record.get("dob") or "")
        subject_id = str(record.get("id") or "person_" + ids.content_hmac(key, name.casefold() + "|" + dob)[:16])
        # Unknown age is treated conservatively. An old authored age cannot keep a
        # now-adult child under parental authority; DOB determines the current age.
        try:
            birthday = date.fromisoformat(dob)
            adult = (today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))) >= 18
        except ValueError:
            adult = True
        output.append(_person(record, subject_id, adult or record is spouse))
    if len({person.subject_id for person in output}) != len(output):
        raise ValueError("household profile contains duplicate identities")
    return output


def _person(record: dict[str, Any], subject_id: str, required: bool) -> Person:
    name = str(record["name"])
    aliases = tuple(dict.fromkeys(str(v) for v in (name, record.get("nickname"), name.split(maxsplit=1)[0]) if v))
    return Person(subject_id, name, aliases, required)
