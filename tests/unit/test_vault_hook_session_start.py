"""D-27: with no watchdog, a session start must make left-over decrypted state visible."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import vault_hook as vh  # noqa: E402


def _point_at(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(vh, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(vh, "LOCK_FILE", str(tmp_path / ".artha-decrypted"))
    monkeypatch.setattr(vh, "SENSITIVE_DOMAINS", ["finance", "kids"])


def test_quiet_when_everything_is_encrypted(tmp_path, monkeypatch, capsys):
    _point_at(tmp_path, monkeypatch)
    vh.hook_session_start()
    assert capsys.readouterr().out == ""


def test_warns_when_a_lock_file_was_left_behind(tmp_path, monkeypatch, capsys):
    _point_at(tmp_path, monkeypatch)
    (tmp_path / ".artha-decrypted").write_text("{}")
    vh.hook_session_start()
    out = capsys.readouterr().out
    assert "[VAULT-WARN]" in out and "earlier session" in out and "vault.py encrypt" in out


def test_warns_about_stray_plaintext_without_a_lock(tmp_path, monkeypatch, capsys):
    _point_at(tmp_path, monkeypatch)
    (tmp_path / "state" / "kids.md").write_text("x")
    vh.hook_session_start()
    out = capsys.readouterr().out
    assert "kids" in out and "finance" not in out


def test_session_start_subcommand_is_wired(tmp_path, monkeypatch, capsys):
    _point_at(tmp_path, monkeypatch)
    (tmp_path / ".artha-decrypted").write_text("{}")
    monkeypatch.setattr(sys, "argv", ["vault_hook.py", "session-start"])
    try:
        vh.main()
    except SystemExit as exc:
        assert exc.code == 0
    assert "[VAULT-WARN]" in capsys.readouterr().out


def test_warns_about_plaintext_copies_of_vaulted_domains(tmp_path, monkeypatch, capsys):
    """Backups and conflict copies of a vaulted domain are plaintext too, whatever their suffix."""
    _point_at(tmp_path, monkeypatch)
    (tmp_path / "state" / "finance.md.pre-write.bak").write_text("balance: 1")
    (tmp_path / "state" / "kids.md-Laptop.bak").write_text("x")
    vh.hook_session_start()
    out = capsys.readouterr().out
    assert "[VAULT-WARN]" in out
    assert "finance.md.pre-write.bak" in out and "kids.md-Laptop.bak" in out


def test_encrypted_copies_and_other_domains_stay_quiet(tmp_path, monkeypatch, capsys):
    _point_at(tmp_path, monkeypatch)
    (tmp_path / "state" / "finance.md.age").write_bytes(b"age-encryption.org/v1\n...")
    (tmp_path / "state" / "finance.md.age.v1").write_bytes(b"age-encryption.org/v1\n...")
    (tmp_path / "state" / "finance.md.age.pre-shrink").write_bytes(b"age-encryption.org/v1\n...")
    (tmp_path / "state" / "financial_plan.md").write_text("not a vaulted domain")
    vh.hook_session_start()
    assert capsys.readouterr().out == ""
