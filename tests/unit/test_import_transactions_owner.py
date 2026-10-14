"""The spouse label comes from the local profile; source carries no household names."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import import_transactions as it  # noqa: E402


def test_label_from_profile(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: "family:\n  spouse:\n    name: Sam Example\n")
    assert it._spouse_label() == "Sam"


def test_label_falls_back_when_profile_is_unusable(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: "family: {}\n")
    assert it._spouse_label() == "Spouse"

    def missing(self, **kw):
        raise FileNotFoundError(self)
    monkeypatch.setattr(Path, "read_text", missing)
    assert it._spouse_label() == "Spouse"
