"""Direct unit tests for scripts/gmail_send.py (HTML conversion, dry-run, send, archive & --health)."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

import gmail_send as gs

ARTHA_TEST_OWNER = {
    "requirements": ["FR-27", "TR-3"],
    "modules": ["scripts/gmail_send.py"],
}


def test_markdown_to_html_converts_headings_bullets_tables_and_escapes_html():
    md = (
        "## Morning <Summary>\n"
        "━━━\n"
        "• **Urgent**: Pay bill & archive\n"
        "| Domain | Status |\n"
        "| --- | --- |\n"
        "| **finance** | ok |\n"
    )
    html = gs._markdown_to_html(md)
    assert "<h2>Morning &lt;Summary&gt;</h2>" in html or "Morning &lt;Summary&gt;" in html
    assert "<hr" in html
    assert "<b>Urgent</b>: Pay bill &amp; archive</li>" in html
    assert "<table " in html and "<th " in html and "<b>finance</b>" in html


def test_archive_briefing_delegates_to_briefing_archive(monkeypatch):
    fake_archive = types.ModuleType("lib.briefing_archive")
    fake_archive.save = lambda body, source, subject: {"status": "ok", "path": "/briefings/2026-10-11.md"}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.briefing_archive", fake_archive)
    res = gs.archive_briefing("body text", "Morning Briefing")
    assert res == {"archived": True, "path": "/briefings/2026-10-11.md"}

    fake_archive.save = lambda *a, **k: {"status": "failed", "error": "blocked"}  # type: ignore[attr-defined]
    assert gs.archive_briefing("x", "subj") == {"archived": False, "error": "blocked"}


def _install_fake_google_auth(monkeypatch, *, send_id="msg-123", send_exc: Exception | None = None):
    sent_payloads = []
    profile_mock = MagicMock()
    profile_mock.execute.return_value = {"emailAddress": "sender@example.com"}

    def fake_send(*, userId, body):
        sent_payloads.append((userId, body))
        req = MagicMock()
        if send_exc is not None:
            req.execute.side_effect = send_exc
        else:
            req.execute.return_value = {"id": send_id}
        return req

    service = MagicMock()
    service.users.return_value.getProfile.return_value = profile_mock
    service.users.return_value.messages.return_value.send.side_effect = fake_send

    mod = types.ModuleType("google_auth")
    mod.build_service = lambda api, ver: service  # type: ignore[attr-defined]
    mod.check_stored_credentials = lambda: {"gmail_token_stored": True}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google_auth", mod)
    return sent_payloads


def test_send_email_dry_run_and_send_and_error(monkeypatch):
    sent = _install_fake_google_auth(monkeypatch, send_id="msg-99")
    monkeypatch.setattr(gs, "archive_briefing", lambda body, subj: {"archived": True, "path": "briefings/x.md"})

    dry = gs.send_email("to@example.com", "Subj", "## Hi\n• **Item**", dry_run=True)
    assert dry["status"] == "dry_run" and dry["from"] == "sender@example.com" and dry["archived"] is True
    assert sent == []

    res = gs.send_email("to@example.com", "Subj", "## Hi\n• **Item**", dry_run=False)
    assert res["status"] == "sent" and res["message_id"] == "msg-99" and len(sent) == 1

    _install_fake_google_auth(monkeypatch, send_exc=RuntimeError("quota"))
    err = gs.send_email("to@example.com", "Subj", "body", archive=False)
    assert err["status"] == "error" and "quota" in err["error"]


def test_main_health_and_validation_exits(monkeypatch, capsys):
    _install_fake_google_auth(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["gmail_send.py", "--health"])
    with pytest.raises(SystemExit) as exc:
        gs.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "gmail_token_stored": True}

    monkeypatch.setattr(sys, "argv", ["gmail_send.py", "--subject", "Hi"])
    with pytest.raises(SystemExit) as exc2:
        gs.main()
    assert exc2.value.code == 1
