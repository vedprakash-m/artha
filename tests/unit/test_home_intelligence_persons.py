"""Household identities come from the local profile, never from source."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from skills import home_intelligence as hi  # noqa: E402


def test_person_entities_come_from_the_profile(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("home_assistant:\n  person_entities:\n    person.a: Alpha\n    person.b: Beta\n")
    assert hi._load_person_entities(p) == {"person.a": "Alpha", "person.b": "Beta"}


def test_missing_or_malformed_profile_tracks_nobody(tmp_path):
    assert hi._load_person_entities(tmp_path / "absent.yaml") == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text("home_assistant: [not, a, mapping]\n")
    assert hi._load_person_entities(bad) == {}
    bad.write_text(": : :\n")
    assert hi._load_person_entities(bad) == {}

