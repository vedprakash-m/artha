"""The Artha MCP server is read-only by construction: no write tools, fixed viewer, strict arguments, data-only output."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from catchup_core import cli
from catchup_core.mcp import SERVER_NAME, TOOLS, handle, serve, tool_runner

WRITE_NAMES = ("forget", "capture", "export", "restore", "pause", "resume", "resolve", "reconcile", "approve", "send",
               "apply", "delete", "write", "bundle")


def call(run, name, arguments=None, ident=1):
    return handle({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments or {}}}, run)


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_no_tool_can_write_send_approve_forget_or_resolve():
    names = {t["name"] for t in TOOLS}
    assert names == {"beliefs", "why", "items", "goals", "health", "activity", "history",
                     "propose_correction", "propose_feedback"}
    assert not [n for n in names if any(w in n for w in WRITE_NAMES)]
    assert all(t["annotations"]["readOnlyHint"] is True and t["annotations"]["destructiveHint"] is False
               and t["inputSchema"]["additionalProperties"] is False for t in TOOLS)
    assert not any("actor" in t["inputSchema"]["properties"] or "viewer" in t["inputSchema"]["properties"] for t in TOOLS)


def test_initialize_negotiates_a_known_protocol_and_advertises_only_tools():
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, None)
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05" and set(result["capabilities"]) == {"tools"}
    assert result["serverInfo"]["name"] == SERVER_NAME
    newer = handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}}, None)
    assert newer["result"]["protocolVersion"] == "2025-06-18"                      # offers its own latest instead


def test_notifications_get_no_response_and_unknown_methods_are_errors():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, None) is None
    assert handle({"jsonrpc": "2.0", "id": 3, "method": "ping"}, None)["result"] == {}
    assert handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, None)["error"]["code"] == -32601
    assert handle([1, 2], None)["error"]["code"] == -32600 and handle({"id": 1}, None)["error"]["code"] == -32600
    assert call(None, "forget")["error"]["code"] == -32602 and call(None, "nope")["error"]["code"] == -32602
    listing = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, None)["result"]["tools"]
    assert len(listing) == len(TOOLS)


def test_arguments_are_validated_strictly_before_anything_runs():
    ran = []
    run = lambda name, args: ran.append((name, args)) or {"ok": True}                  # noqa: E731
    for name, args in (("why", {}), ("why", {"fact_id": ""}), ("why", {"fact_id": 5}), ("why", {"fact_id": "x" * 500}),
                       ("beliefs", {"actor": "someone-else"}), ("propose_feedback", {"obligation": "o", "response": "delete",
                                                                                     "reason": "r"})):
        response = call(run, name, args)
        assert response["result"]["isError"] is True, (name, args)
    assert ran == []
    assert call(run, "beliefs")["result"]["isError"] is False and ran == [("beliefs", {})]


def test_read_tools_map_to_commands_and_proposals_never_apply():
    seen = []

    def execute(namespace):
        seen.append(namespace)
        if namespace.command == "correct":
            return {"before": "2026-10-14", "after": "2026-10-16", "apply_version": "v1"}
        return {namespace.command: [{"fact_id": "f1"}]}
    run = tool_runner(execute)
    assert payload(call(run, "beliefs", {"domain": "kids_school"}))["beliefs"][0]["fact_id"] == "f1"
    assert seen[0].domain == "kids_school" and seen[0].command == "beliefs"
    draft = payload(call(run, "propose_correction", {"fact_id": "f1", "value": "2026-10-16"}))
    assert draft["applied"] is False and draft["draft"]["apply_version"] == "v1" and seen[-1].apply is None
    fb = payload(call(run, "propose_feedback", {"obligation": "o", "response": "dismiss", "reason": "handled"}))
    assert fb["applied"] is False and len(seen) == 2                                    # feedback ran no command at all


def test_results_are_inert_bounded_and_errors_leak_nothing():
    def hostile(_name, _args):
        return {"quote": "see https://evil.example/x?d=secret ![i](http://evil.example/p.png) <img src=x>"}
    text = payload(call(hostile, "beliefs"))["quote"]
    assert "http" not in text and "<img" not in text and "![" not in text

    def huge(_name, _args):
        return {f"k{i}": "x" * 4000 for i in range(40)}            # each string is capped at 4,000; the total is capped too
    assert payload(call(huge, "beliefs"))["truncated"] is True
    single = payload(call(lambda *_a: {"blob": "x" * 200_000}, "beliefs"))["blob"]
    assert len(single) <= 4000                                          # one value can never dominate

    def boom(_name, _args):
        raise OSError("/Users/secret/path/ledger.db is locked")
    response = call(boom, "beliefs")
    assert response["result"]["isError"] is True and "/Users" not in json.dumps(response)


def test_serve_speaks_newline_delimited_json_rpc_and_survives_garbage():
    lines = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        "not json", "", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        "x" * 1_000_001,
    ]) + "\n"
    out = io.StringIO()
    assert serve(io.StringIO(lines), out, lambda *_a: {}) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("id") for r in responses] == [1, None, 2, None]
    assert responses[1]["error"]["code"] == -32700 and responses[3]["error"]["code"] == -32600


def test_cli_mcp_command_refuses_to_start_without_configuration(monkeypatch):
    from catchup_core.runtime import RuntimeConfigError

    def missing(_path):
        raise RuntimeConfigError("no config")
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config", missing)
    err = io.StringIO()
    assert cli.main(["mcp"], stdin=io.StringIO(""), out=io.StringIO(), err=err) == 30
    assert "not configured" in err.getvalue()


def test_cli_mcp_runs_each_call_through_the_shared_inspection_path(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("catchup_core.runtime.load_runtime_config", lambda _p: SimpleNamespace(cutover=False))
    seen = []
    monkeypatch.setattr(cli, "_run_inspection", lambda ns: seen.append(ns) or {"history": []})
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "history", "arguments": {}}})
    out = io.StringIO()
    assert cli.main(["mcp"], stdin=io.StringIO(request + "\n"), out=out, err=io.StringIO()) == 0
    assert seen[0].command == "history" and seen[0].shadow is True and seen[0].actor is None
    assert json.loads(json.loads(out.getvalue())["result"]["content"][0]["text"]) == {"history": []}


def test_a_real_stdio_process_round_trips_without_extra_output():
    """The transport contract: the only bytes on stdout are protocol lines."""
    scripts = Path(cli.__file__).resolve().parents[1]
    code = ("import sys, io\n"
            f"sys.path.insert(0, {str(scripts)!r})\n"
            "from catchup_core.mcp import serve\n"
            "sys.exit(serve(sys.stdin, sys.stdout, lambda n, a: {'echo': n}))\n")
    request = "\n".join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                         json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": "health", "arguments": {}}})]) + "\n"
    done = subprocess.run([sys.executable, "-c", code], input=request, capture_output=True, text=True, timeout=30,
                          check=False)
    lines = done.stdout.splitlines()
    assert done.returncode == 0 and len(lines) == 2 and done.stderr == ""
    assert json.loads(json.loads(lines[1])["result"]["content"][0]["text"]) == {"echo": "health"}


@pytest.mark.parametrize("name", [t["name"] for t in TOOLS])
def test_every_tool_has_a_description_and_a_closed_schema(name):
    tool = next(t for t in TOOLS if t["name"] == name)
    assert len(tool["description"]) > 20 and tool["inputSchema"]["type"] == "object"
