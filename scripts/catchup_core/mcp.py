"""Artha-owned read-only MCP server over stdio (re-artha 9.1, D-15, Stage 5 trial).

Launched on demand by the user's own assistant (``cli.py mcp``); it is a short-lived stdio process, never a listener.
It lets an approved assistant *read* permission-filtered records and *draft* proposals, and nothing else:

* every tool is read-only; there is no tool that writes State Files, sends, approves, forgets, exports, captures,
  pauses or resolves anything.  ``propose_*`` tools return an inert draft that the owner applies with the CLI;
* the viewer is fixed by the local configuration, never a tool argument, so an assistant cannot ask as someone else;
* results are data-only (``views.safe_projection``: no links, images or markup) and size-capped, and tool arguments
  are validated strictly; tool annotations are advisory hints, enforcement is the absence of write tools;
* a protocol error never exposes stack traces or file paths.

Wire format: newline-delimited JSON-RPC 2.0 (MCP stdio transport).  Implemented subset: ``initialize``, ``ping``,
``tools/list``, ``tools/call`` and the ``notifications/*`` family (ignored).
"""
from __future__ import annotations

import json
from argparse import Namespace
from collections.abc import Callable, Mapping
from typing import Any, TextIO

from catchup_core import CORE_CONTRACT_VERSION

__all__ = ["SERVER_NAME", "TOOLS", "handle", "serve", "tool_runner"]

SERVER_NAME = "artha-readonly"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 60_000
_MAX_LINE = 1_000_000

_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


def _tool(name: str, description: str, properties: dict[str, Any] | None = None,
          required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": name, "description": description, "annotations": dict(_ANNOTATIONS),
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required),
                            "additionalProperties": False}}


_STR = {"type": "string", "maxLength": 200}
TOOLS: tuple[dict[str, Any], ...] = (
    _tool("beliefs", "Facts Artha currently holds, with source, observation date and provenance (permission-filtered).",
          {"domain": _STR, "subject": _STR}),
    _tool("why", "Explain one Fact: its evidence, history and what was known at a past time.",
          {"fact_id": _STR, "as_of": _STR}, ("fact_id",)),
    _tool("items", "Open Items from the curated State File."),
    _tool("goals", "Goals with authored fields and evidence-backed observations."),
    _tool("health", "Capability states, coverage, uninterpreted Signals, latency and cost estimates."),
    _tool("activity", "Action proposals by lifecycle state with reversibility; read-only."),
    _tool("history", "Published Briefings (identity and time only)."),
    _tool("propose_correction", "Draft a correction to a Fact and show the exact diff. Nothing is changed; the owner "
          "applies it with the CLI.", {"fact_id": _STR, "value": _STR}, ("fact_id", "value")),
    _tool("propose_feedback", "Draft feedback (useful, dismiss, snooze, acknowledge) on an Alert. Nothing is changed.",
          {"obligation": _STR, "response": {"type": "string", "enum": ["useful", "dismiss", "snooze", "acknowledge"]},
           "reason": _STR}, ("obligation", "response", "reason")),
)
_BY_NAME = {t["name"]: t for t in TOOLS}
_READ = {"beliefs", "why", "items", "goals", "health", "activity", "history"}


def _validate(tool: Mapping[str, Any], arguments: Any) -> dict[str, str]:
    if arguments is None:
        arguments = {}
    schema = tool["inputSchema"]
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    unknown = set(arguments) - set(schema["properties"])
    if unknown:
        raise ValueError(f"unknown argument: {sorted(unknown)[0]}")
    for name in schema["required"]:
        if not isinstance(arguments.get(name), str) or not arguments[name].strip():
            raise ValueError(f"{name} is required")
    clean: dict[str, str] = {}
    for name, value in arguments.items():
        spec = schema["properties"][name]
        if not isinstance(value, str) or len(value) > spec.get("maxLength", 200):
            raise ValueError(f"{name} must be a short string")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{name} must be one of {', '.join(spec['enum'])}")
        clean[name] = value
    return clean


def tool_runner(execute: Callable[[Namespace], dict[str, Any]]) -> Callable[[str, dict[str, str]], Any]:
    """Map a validated tool call onto the existing command surface; ``execute`` owns storage, locking and viewer."""
    def run(name: str, args: dict[str, str]) -> Any:
        if name in _READ:
            return execute(Namespace(command=name, domain=args.get("domain"), subject=args.get("subject"),
                                     as_of=args.get("as_of"), fact_id=args.get("fact_id"), days=7))
        if name == "propose_correction":
            preview = execute(Namespace(command="correct", fact_id=args["fact_id"], value=args["value"], apply=None,
                                        domain=None, subject=None, as_of=None, days=7))
            return {"draft": preview, "applied": False, "to_apply": "the owner reviews this diff, then runs "
                    "`cli.py correct <fact_id> --value <value> --apply <apply_version>`"}
        if name == "propose_feedback":
            return {"draft": {"obligation": args["obligation"], "response": args["response"], "reason": args["reason"]},
                    "applied": False,
                    "to_apply": "the owner runs `cli.py feedback <obligation> <response> --reason ...`"}
        raise ValueError("unknown tool")
    return run


def _text_result(value: Any, *, error: bool = False) -> dict[str, Any]:
    from catchup_core.views import safe_projection  # noqa: PLC0415
    data = safe_projection(value) if isinstance(value, Mapping) else value
    text = json.dumps(data, default=str, sort_keys=True)
    if len(text) > MAX_RESULT_CHARS:
        text = json.dumps({"truncated": True, "note": "Result too large; narrow the request (domain or subject).",
                           "preview": text[: MAX_RESULT_CHARS // 4]})
    return {"content": [{"type": "text", "text": text}], "isError": error}


class _RpcError(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(text)
        self.code, self.text = code, text


def _dispatch(method: Any, params: Any, run: Callable[[str, dict[str, str]], Any]) -> Any:
    """Result for one request; raises ``_RpcError`` for protocol-level failures."""
    if method == "initialize":
        wanted = params.get("protocolVersion") if isinstance(params, dict) else None
        return {"protocolVersion": wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": CORE_CONTRACT_VERSION},
                "instructions": "Read-only access to the owner's Artha records. Proposals are drafts only."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": list(TOOLS)}
    if method != "tools/call":
        raise _RpcError(-32601, "Method not found")
    if not isinstance(params, dict) or params.get("name") not in _BY_NAME:
        raise _RpcError(-32602, "Unknown tool")
    try:
        args = _validate(_BY_NAME[params["name"]], params.get("arguments"))
    except ValueError as exc:
        return _text_result({"error": str(exc)}, error=True)
    try:
        return _text_result(run(params["name"], args))
    except Exception as exc:
        return _text_result({"error": f"{type(exc).__name__}: the request could not be completed"}, error=True)


def handle(message: Any, run: Callable[[str, dict[str, str]], Any]) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response (or None for a notification) out.  Never raises."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    ident = message.get("id")
    if ident is None:
        return None                                              # notification: no response, whatever it says
    try:
        result = _dispatch(message.get("method"), message.get("params") or {}, run)
        return {"jsonrpc": "2.0", "id": ident, "result": result}
    except _RpcError as exc:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": exc.code, "message": exc.text}}


def serve(stdin: TextIO, stdout: TextIO, run: Callable[[str, dict[str, str]], Any]) -> int:
    """Read newline-delimited JSON-RPC until EOF; protocol output is the only thing written to stdout."""
    for line in stdin:
        if not line.strip():
            continue
        if len(line) > _MAX_LINE:
            response: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None,
                                               "error": {"code": -32600, "message": "Request too large"}}
        else:
            try:
                response = handle(json.loads(line), run)
            except ValueError:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
