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


def test_read_raw_enforces_vault_policy(tmp_path, monkeypatch):
    goals = tmp_path / "goals.md"
    goals.write_text("# Goals\nAll good.\n", encoding="utf-8")
    sealed = tmp_path / "finance.md.age"
    sealed.write_text("encrypted-content", encoding="utf-8")

    monkeypatch.setattr(state_reader, "_vault_required_paths", lambda: frozenset({str(sealed.resolve())}))
    reader = state_reader.StateReader()
    assert "All good." in reader.read_raw(goals)
    with pytest.raises(state_reader.VaultPolicyViolation, match="vault-required"):
        reader.read_raw(sealed)


def test_read_raw_refuses_plaintext_counterpart_of_vault_required_file(tmp_path, monkeypatch):
    plaintext = tmp_path / "finance.md"
    plaintext.write_text("---\ndomain: finance\n---\n", encoding="utf-8")
    sealed = tmp_path / "finance.md.age"
    monkeypatch.setattr(
        state_reader,
        "_vault_required_paths",
        lambda: frozenset({str(plaintext.resolve()), str(sealed.resolve())}),
    )
    with pytest.raises(state_reader.VaultPolicyViolation, match="vault-required"):
        state_reader.StateReader().read_raw(plaintext)


def test_nested_vault_domain_uses_its_actual_parent_directory(tmp_path, monkeypatch):
    sealed = tmp_path / "state" / "work" / "work-career.md.age"
    sealed.parent.mkdir(parents=True)
    sealed.write_bytes(b"x")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="synthetic work body", stderr="")

    monkeypatch.setattr(state_reader.subprocess, "run", fake_run)
    assert state_reader.StateReader._decrypt("work-career", sealed) == "synthetic work body"
    assert seen["argv"][-2:] == ["--state-dir", str(sealed.parent)]


def test_registry_revision_is_reloaded_and_nested_values_are_isolated(tmp_path, monkeypatch):
    registry = tmp_path / "domain_registry.yaml"
    registry.write_text(
        "domains:\n  goals:\n    state_file: state/goals.md\n"
        "    nested:\n      values: [one]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(state_reader, "_REGISTRY_PATH", registry)
    monkeypatch.setattr(state_reader, "_REGISTRY_CACHE", None)
    monkeypatch.setattr(state_reader, "_REGISTRY_REVISION", None)
    first = state_reader._domain_cfg("goals")
    first["nested"]["values"].append("private")
    assert state_reader._domain_cfg("goals")["nested"]["values"] == ["one"]
    registry.write_text(
        "domains:\n  goals:\n    state_file: state/goals-v2.md\n",
        encoding="utf-8",
    )
    assert state_reader._domain_cfg("goals")["state_file"] == "state/goals-v2.md"
