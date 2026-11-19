"""Synthetic RF-18 coverage for the local Work document MCP server."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mcp_sharepoint_server as server


def _permit(monkeypatch, root: Path, *, subject: str = "synthetic-work-user") -> None:
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OneDriveCommercial", str(root))
    monkeypatch.setenv("USERNAME", subject)
    monkeypatch.setenv("ARTHA_WORK_PROFILE", "work")
    monkeypatch.setenv("ARTHA_WORK_SUBJECT", subject)
    monkeypatch.setattr(server, "_load_work_redaction_policy", lambda: ())


def test_root_and_file_authority_are_resolved_for_each_request(monkeypatch, tmp_path):
    first = tmp_path / "commercial-one"
    second = tmp_path / "commercial-two"
    first.mkdir()
    second.mkdir()
    (first / "first.md").write_text("first", encoding="utf-8")
    (second / "second.md").write_text("second", encoding="utf-8")

    _permit(monkeypatch, first)
    one = server._tool_list_recent_documents({"limit": 5})
    assert [item["name"] for item in one["files"]] == ["first.md"]

    monkeypatch.setenv("OneDriveCommercial", str(second))
    two = server._tool_list_recent_documents({"limit": 5})
    assert [item["name"] for item in two["files"]] == ["second.md"]
    assert two["root"] == str(second.resolve())
    assert not hasattr(server, "_WORK_ROOT")


def test_generic_onedrive_and_personal_roots_are_not_work_authority(monkeypatch, tmp_path):
    personal = tmp_path / "OneDrive"
    personal.mkdir()
    (personal / "personal.md").write_text("must not be listed", encoding="utf-8")
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setenv("OneDrive", str(personal))
    monkeypatch.setenv("USERNAME", "synthetic-work-user")

    result = server._tool_list_recent_documents({"limit": 5})
    assert result == {"error": "Work document read authorization unavailable"}

    monkeypatch.setenv("OneDriveCommercial", str(personal))
    result = server._tool_list_recent_documents({"limit": 5})
    assert result == {"error": "Work document read authorization unavailable"}


def test_ambiguous_work_roots_and_forged_context_are_refused(monkeypatch, tmp_path):
    commercial = tmp_path / "commercial"
    second = tmp_path / "second-commercial"
    _permit(monkeypatch, commercial)
    second.mkdir()
    monkeypatch.setenv(
        "OneDriveCommercial",
        f"{commercial}{os.pathsep}{second}",
    )

    assert server._tool_list_recent_documents({}) == {
        "error": "Work document read authorization unavailable"
    }

    _permit(monkeypatch, commercial)
    monkeypatch.setenv("ARTHA_WORK_PROFILE", "personal")
    assert server._tool_list_recent_documents({}) == {
        "error": "Work document read authorization unavailable"
    }

    monkeypatch.setenv("ARTHA_WORK_PROFILE", "work")
    monkeypatch.setenv("ARTHA_WORK_SUBJECT", "another-user")
    assert server._tool_list_recent_documents({}) == {
        "error": "Work document read authorization unavailable"
    }


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list", {"limit": 0}),
        ("list", {"limit": -1}),
        ("list", {"limit": 101}),
        ("search", {"query": "roadmap", "limit": -1}),
        ("search", {"query": "roadmap", "limit": 51}),
        ("read", {"path": "relative.md"}),
    ],
)
def test_invalid_limits_and_nonabsolute_paths_are_refused(monkeypatch, tmp_path, tool, arguments):
    root = tmp_path / "commercial"
    _permit(monkeypatch, root)
    handlers = {
        "list": server._tool_list_recent_documents,
        "search": server._tool_search_documents,
        "read": server._tool_read_document,
    }
    assert "error" in handlers[tool](arguments)


def test_authorized_display_reads_are_bounded_redacted_and_provenance_limited(monkeypatch, tmp_path):
    root = tmp_path / "commercial"
    document = root / "docs" / "roadmap.md"
    document.parent.mkdir(parents=True)
    document.write_text("synthetic public line\nProject Midnight is synthetic\n", encoding="utf-8")
    _permit(monkeypatch, root)
    monkeypatch.setattr(server, "_load_work_redaction_policy", lambda: ("Project Midnight",))

    listed = server._tool_list_recent_documents({"limit": 10, "extensions": [".md"]})
    searched = server._tool_search_documents({"query": "synthetic", "limit": 10})
    read = server._tool_read_document({"path": str(document), "max_chars": 10_000})

    assert listed["count"] == 1
    assert searched["count"] == 1
    assert "[REDACTED] is synthetic" in read["text"]
    for result in (listed, searched, read):
        assert result["profile"] == "work"
        assert result["subject"] == "synthetic-work-user"
        assert result["displayOnly"] is True
        assert result["provenance"] == {
            "source": "local-onedrive-commercial",
            "classification": "untrusted-work-display",
            "persistent": False,
        }


def test_read_checks_root_and_redaction_before_path_or_content_disclosure(monkeypatch, tmp_path):
    root = tmp_path / "commercial"
    outside = tmp_path / "outside.txt"
    _permit(monkeypatch, root)
    outside.write_text("secret", encoding="utf-8")

    result = server._tool_read_document({"path": str(outside)})
    assert result == {"error": "Work document read authorization unavailable"}

    monkeypatch.setattr(
        server,
        "_load_work_redaction_policy",
        lambda: (_ for _ in ()).throw(RuntimeError("policy unavailable")),
    )
    result = server._tool_read_document({"path": str(root / "missing.md")})
    assert result == {"error": "Work document read authorization unavailable"}
    assert "outside.txt" not in json.dumps(result)


def test_root_substitution_after_authorization_is_refused(monkeypatch, tmp_path):
    root = tmp_path / "commercial"
    document = root / "roadmap.md"
    document.parent.mkdir(parents=True)
    document.write_text("original synthetic content", encoding="utf-8")
    _permit(monkeypatch, root)

    def substitute():
        moved = tmp_path / "moved-commercial"
        root.rename(moved)
        root.mkdir()
        (root / "roadmap.md").write_text("substituted synthetic content", encoding="utf-8")
        return ()

    monkeypatch.setattr(server, "_load_work_redaction_policy", substitute)
    assert server._tool_read_document({"path": str(document)}) == {
        "error": "Work document read authorization unavailable"
    }


def test_read_and_scan_limiters_refuse_without_partial_output(monkeypatch, tmp_path):
    root = tmp_path / "commercial"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "one.md").write_text("needle one", encoding="utf-8")
    (root / "docs" / "two.md").write_text("needle two", encoding="utf-8")
    _permit(monkeypatch, root)

    monkeypatch.setattr(server, "_MAX_FILE_BYTES", 5)
    assert server._tool_read_document({"path": str(root / "docs" / "one.md")}) == {
        "error": "Work document read authorization unavailable"
    }

    monkeypatch.setattr(server, "_MAX_FILE_BYTES", 32)
    monkeypatch.setattr(server, "_MAX_SCAN_BYTES", 5)
    assert server._tool_search_documents({"query": "needle"}) == {
        "error": "Work document read authorization unavailable"
    }


def test_strict_rpc_rejects_unknown_arguments_and_bounds_requests(monkeypatch, tmp_path):
    root = tmp_path / "commercial"
    _permit(monkeypatch, root)
    response = server._handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "list_recent_documents",
            "arguments": {"viewer": "someone-else"},
        },
    })
    assert response["result"]["isError"] is True

    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "ping",
        "params": {},
    }
    oversized = "x" * 1_000_001 + "\n"
    stdin = io.StringIO(json.dumps(request) + "\n" + oversized)
    stdout = io.StringIO()
    assert server.serve(stdin, stdout) == 0
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["result"] == {}
    assert responses[1]["error"]["code"] == -32600
