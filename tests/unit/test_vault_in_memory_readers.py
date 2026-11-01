"""Readers of encrypted State Files use the in-memory vault; none writes plaintext or re-encrypts."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import domain_view
import vault


def test_domain_view_reads_a_sealed_domain_in_memory_and_never_calls_decrypt_or_encrypt(tmp_path, monkeypatch):
    (tmp_path / "finance.md.age").write_bytes(b"sealed")
    monkeypatch.setattr(domain_view, "_STATE_DIR", tmp_path)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="---\nstatus: green\n---\nsynthetic", stderr="")
    monkeypatch.setattr(domain_view.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["domain_view.py", "finance"])
    assert domain_view.main() == 0
    assert calls and all(argv[-2:] == ["read", "finance"] for argv in calls)
    assert not (tmp_path / "finance.md").exists()


def test_domain_view_fails_closed_when_the_sealed_domain_cannot_be_read(tmp_path, monkeypatch, capsys):
    (tmp_path / "finance.md.age").write_bytes(b"sealed")
    monkeypatch.setattr(domain_view, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(domain_view.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="no key"))
    monkeypatch.setattr(sys, "argv", ["domain_view.py", "finance"])
    assert domain_view.main() == 1
    assert "could not be read" in capsys.readouterr().err
    assert not (tmp_path / "finance.md").exists()


def test_the_guardrail_treats_a_sealed_domain_as_readable_without_a_plaintext_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "ARTHA_DIR", tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "finance.md.age").write_bytes(b"sealed")
    result = vault.check_file_readable("state/finance.md")
    assert result["readable"] is True and result.get("sealed") is True
    missing = vault.check_file_readable("state/goals.md")
    assert missing["readable"] is False and missing["reason"] == "file_missing"


def test_vault_decrypt_refuses_without_the_offline_edit_flag(monkeypatch, capsys):
    import pytest  # noqa: PLC0415
    called = []
    monkeypatch.setattr(vault, "do_decrypt", lambda: called.append(1))
    monkeypatch.setattr(sys, "argv", ["vault.py", "decrypt"])
    with pytest.raises(SystemExit) as refused:
        vault.main()
    assert refused.value.code == 2 and not called and "--offline-edit" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["vault.py", "decrypt", "--offline-edit"])
    vault.main()
    assert called == [1]
