"""StateReader reads encrypted domains in memory (vault.py read); it never asks for plaintext on disk."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import state_reader


def test_an_encrypted_domain_is_read_through_the_in_memory_vault_not_decrypted_to_disk(tmp_path, monkeypatch):
    sealed = tmp_path / "finance.md.age"
    sealed.write_bytes(b"x")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="synthetic body", stderr="")
    monkeypatch.setattr(state_reader.subprocess, "run", fake_run)
    assert state_reader.StateReader._decrypt("finance", sealed) == "synthetic body"
    assert seen["argv"][-2:] == ["read", "finance"] and "decrypt" not in seen["argv"]


def test_a_failed_in_memory_read_is_a_policy_violation_with_no_plaintext_fallback(tmp_path, monkeypatch):
    sealed = tmp_path / "finance.md.age"
    sealed.write_bytes(b"x")
    monkeypatch.setattr(state_reader.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no key"))
    with pytest.raises(state_reader.VaultPolicyViolation):
        state_reader.StateReader._decrypt("finance", sealed)
    assert not Path(tmp_path / "finance.md").exists()
    assert subprocess  # imported for the monkeypatch target
