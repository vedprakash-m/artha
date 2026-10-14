"""The spouse label comes from the local profile; source carries no household names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import import_transactions as it  # noqa: E402


def test_label_from_profile(tmp_path):
    (tmp_path / "user_profile.yaml").write_text("family:\n  spouse:\n    name: Sam Example\n")
    assert it._spouse_label(str(tmp_path)) == "Sam"


def test_label_falls_back_when_profile_is_unusable(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    assert it._spouse_label(str(empty)) == "Spouse"
    odd = tmp_path / "o"
    odd.mkdir()
    (odd / "user_profile.yaml").write_text("family: {}\n")
    assert it._spouse_label(str(odd)) == "Spouse"
