"""Skills must read protected domains through the in-memory vault when no plaintext State File exists."""
from __future__ import annotations

import importlib
import sys
import types

from skills.base_skill import read_state_text


def _fake_foundation(monkeypatch, text=None, boom=False):
    class Vault:
        def read(self, domain):
            if boom:
                raise SystemExit(1)
            return text

    fake = types.SimpleNamespace(get_age_vault=lambda state_dir=None: Vault())
    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda n, *a, **k: fake if n == "foundation" else real(n, *a, **k))


def test_plaintext_file_wins(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "immigration.md").write_text("plain text")
    (tmp_path / "state" / "immigration.md.age").write_bytes(b"x")
    assert read_state_text(tmp_path, "immigration") == "plain text"


def test_encrypted_domain_is_decrypted_in_memory_when_no_plaintext(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "immigration.md.age").write_bytes(b"x")
    _fake_foundation(monkeypatch, text="decrypted body")
    assert read_state_text(tmp_path, "immigration") == "decrypted body"
    assert not (tmp_path / "state" / "immigration.md").exists()


def test_missing_domain_returns_empty_without_touching_the_vault(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    _fake_foundation(monkeypatch, boom=True)
    assert read_state_text(tmp_path, "immigration") == ""


def test_vault_failure_degrades_to_empty_instead_of_crashing(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "finance.md.age").write_bytes(b"x")
    _fake_foundation(monkeypatch, boom=True)
    assert read_state_text(tmp_path, "finance") == ""
