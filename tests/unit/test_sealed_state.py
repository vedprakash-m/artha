"""lib.sealed_state: encrypted State Files are read and written through vault.py, in memory."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import sealed_state


def _fake(monkeypatch, calls, returncode=0, stdout="body"):
    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("input")))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    monkeypatch.setattr(sealed_state.subprocess, "run", run)


def test_read_uses_vault_read_and_prefers_nothing_on_disk(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    (tmp_path / "finance.md.age").write_bytes(b"x")
    assert sealed_state.read_domain("finance", tmp_path) == "body"
    assert calls[0][0][-2:] == ["read", "finance"] and not (tmp_path / "finance.md").exists()


def test_a_plaintext_state_file_is_used_when_there_is_no_sealed_one(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    (tmp_path / "goals.md").write_text("plain", encoding="utf-8")
    assert sealed_state.read_domain("goals", tmp_path) == "plain" and not calls


def test_write_passes_the_text_on_stdin_and_creates_no_plaintext_file(tmp_path, monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    sealed_state.write_domain("finance", "new body", tmp_path)
    argv, stdin = calls[0]
    assert argv[-2:] == ["write", "finance"] and stdin == "new body" and not list(tmp_path.glob("*.md"))


def test_failures_raise_and_never_fall_back_to_plaintext(tmp_path, monkeypatch):
    (tmp_path / "finance.md.age").write_bytes(b"x")
    _fake(monkeypatch, [], returncode=1, stdout="")
    with pytest.raises(sealed_state.SealedStateError):
        sealed_state.read_domain("finance", tmp_path)
    with pytest.raises(sealed_state.SealedStateError):
        sealed_state.write_domain("finance", "x", tmp_path)
    assert sealed_state.available("finance", tmp_path) is False
    assert isinstance(tmp_path, Path)


def test_the_importer_updates_finance_and_transactions_only_through_the_sealed_vault(tmp_path, monkeypatch):
    import import_transactions as imp  # noqa: PLC0415
    store = {"finance": "# Finance\n\n## Transaction History\nold row\n\n## Archive\nkept\n"}
    monkeypatch.setattr(imp, "STATE_DIR", tmp_path)
    monkeypatch.setattr(imp.sealed_state, "read_domain", lambda domain, d=None: store[domain])
    monkeypatch.setattr(imp.sealed_state, "write_domain", lambda domain, text, d=None: store.__setitem__(domain, text))
    imp.update_finance_md("## Transaction History\nnew row\n", dry_run=False)
    imp.write_transactions_md("synthetic ledger\n", dry_run=False)
    assert "new row" in store["finance"] and "kept" in store["finance"] and store["transactions"] == "synthetic ledger\n"
    assert not list(tmp_path.iterdir())


def test_the_capital_agent_reads_a_sealed_finance_file_in_memory(tmp_path, monkeypatch):
    from agents import capital_agent  # noqa: PLC0415
    (tmp_path / "finance.md.age").write_bytes(b"x")
    monkeypatch.setattr(capital_agent.sealed_state, "available", lambda d, s=None: True)
    monkeypatch.setattr(capital_agent.sealed_state, "read_domain", lambda d, s=None: "| 2026-10-01 | rent | 100 | expense |\n")
    assert capital_agent._check_vault_plaintext_available(tmp_path, "finance") is True
    assert capital_agent._parse_finance_md(tmp_path / "finance.md") is not None
    assert not (tmp_path / "finance.md").exists()
    monkeypatch.setattr(capital_agent.sealed_state, "available", lambda d, s=None: False)
    assert capital_agent._check_vault_plaintext_available(tmp_path, "finance") is False
