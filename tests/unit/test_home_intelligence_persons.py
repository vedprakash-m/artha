"""Household identities come from the local profile, never from source."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from skills import home_intelligence as hi  # noqa: E402


def test_person_entities_come_from_the_profile(tmp_path):
    (tmp_path / "user_profile.yaml").write_text(
        "home_assistant:\n  person_entities:\n    person.a: Alpha\n    person.b: Beta\n")
    assert hi._load_person_entities(str(tmp_path)) == {"person.a": "Alpha", "person.b": "Beta"}


def test_missing_or_malformed_profile_tracks_nobody(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert hi._load_person_entities(str(empty)) == {}
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "user_profile.yaml").write_text("home_assistant: [not, a, mapping]\n")
    assert hi._load_person_entities(str(bad)) == {}
    (bad / "user_profile.yaml").write_text(": : :\n")
    hi_cfg = __import__("lib.config_loader", fromlist=["invalidate"])
    hi_cfg.invalidate()
    assert hi._load_person_entities(str(bad)) == {}

