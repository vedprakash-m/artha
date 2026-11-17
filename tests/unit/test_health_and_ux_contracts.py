"""TR-3 & UX contracts: --health flags across CLI entrypoints + command routing parity."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

ARTHA_TEST_OWNER = {
    "requirements": ["TR-3", "INFRA-UX"],
    "modules": [
        "scripts/pipeline.py",
        "scripts/gmail_send.py",
        "scripts/setup_msgraph_oauth.py",
        "scripts/setup_icloud_auth.py",
        "scripts/action_orchestrator.py",
        "scripts/catchup_core/cli.py",
    ],
}


def test_all_five_cli_health_contracts(tmp_path, monkeypatch, capsys):
    # 1. gmail_send --health
    import gmail_send as gs
    monkeypatch.setitem(sys.modules, "google_auth", types.SimpleNamespace(
        check_stored_credentials=lambda: {"gmail_token_stored": True},
    ))
    monkeypatch.setattr(sys, "argv", ["gmail_send.py", "--health"])
    with pytest.raises(SystemExit) as e1:
        gs.main()
    assert e1.value.code == 0 and json.loads(capsys.readouterr().out)["status"] == "ok"

    # 2. action_orchestrator --health
    import action_orchestrator as ao
    monkeypatch.setattr(ao, "cmd_health", lambda artha_dir: 0)
    monkeypatch.setattr(ao, "_ARTHA_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["action_orchestrator.py", "--health"])
    assert ao.main() == 0

    # 3. pipeline --health
    import pipeline as pl
    monkeypatch.setattr(pl, "load_connectors_config", lambda: {"connectors": {}})
    monkeypatch.setattr(pl, "run_health_checks", lambda cfg, source_filter=None, verbose=False: 0)
    monkeypatch.setattr(pl, "_ingest_pending_briefs", lambda: None)
    monkeypatch.setattr(sys, "argv", ["pipeline.py", "--health"])
    assert pl.main() == 0

    # 4. setup_msgraph_oauth --health
    import setup_msgraph_oauth as ms
    monkeypatch.setattr(ms, "run_health_check", lambda: print("msgraph ok"))
    monkeypatch.setattr(sys, "argv", ["setup_msgraph_oauth.py", "--health"])
    ms.main()
    assert "msgraph ok" in capsys.readouterr().out

    # 5. setup_icloud_auth --health
    import setup_icloud_auth as ic
    monkeypatch.setattr(ic, "run_health_check", lambda: print("icloud ok"))
    monkeypatch.setattr(sys, "argv", ["setup_icloud_auth.py", "--health"])
    ic.main()
    assert "icloud ok" in capsys.readouterr().out


def test_ux_command_routing_parity_across_docs_and_cli():
    cmds_md = (_ROOT / "config" / "commands.md").read_text(encoding="utf-8")
    gemini_md = (_ROOT / "GEMINI.md").read_text(encoding="utf-8")

    for primary in ("brief", "work", "items", "goals", "domain", "content", "guide", "health"):
        assert f"/{primary}" in cmds_md, f"/{primary} missing from config/commands.md"
        assert primary in gemini_md, f"{primary} routing missing from GEMINI.md"

    from catchup_core.cli import parse_args
    for subcmd in ("catchup", "items", "goals", "health", "beliefs", "mcp"):
        ns = parse_args([subcmd])
        assert ns.command == subcmd

    artha_md = _ROOT / "config" / "Artha.md"
    if artha_md.exists():
        artha_txt = artha_md.read_text(encoding="utf-8")
        assert "config/commands.md" in artha_txt or "catchup" in artha_txt
