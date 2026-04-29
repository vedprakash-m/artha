#!/usr/bin/env python3
"""Passive MCP config discovery for Artha.

This script inventories MCP client configuration files only. It does not start
servers, test network endpoints, read environment variable values, or import
connector code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover - optional fallback
        tomllib = None  # type: ignore[assignment]


SCHEMA_VERSION = "1.0"
MAX_CONFIG_BYTES = 1_048_576
DEFAULT_OUTPUT = "tmp/mcp_discovery.json"

_SERVER_MAP_KEYS = ("servers", "mcpServers", "mcp_servers")
_SERVER_HINT_KEYS = {"command", "url", "endpoint", "type", "transport", "args"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _display_path(path: Path, artha_dir: Path, home_dir: Path) -> str:
    resolved = path.expanduser().resolve()
    for prefix, base in (("$ARTHA_DIR", artha_dir), ("~", home_dir)):
        try:
            rel = resolved.relative_to(base.expanduser().resolve())
            return f"{prefix}/{rel.as_posix()}"
        except ValueError:
            continue
    return resolved.name


def _scope_for(path: Path, artha_dir: Path) -> str:
    try:
        path.expanduser().resolve().relative_to(artha_dir.expanduser().resolve())
        return "project"
    except ValueError:
        return "home"


def _provider_for(path: Path) -> str:
    lowered = "/".join(part.lower() for part in path.parts)
    if ".vscode" in lowered:
        return "vscode"
    if "warp" in lowered:
        return "warp"
    if "claude" in lowered:
        return "claude"
    if "cursor" in lowered:
        return "cursor"
    if "codex" in lowered:
        return "codex"
    return "generic"


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments while preserving string contents."""
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                if text[i] in "\r\n":
                    out.append(text[i])
                i += 1
            i += 2
            continue

        out.append(ch)
        i += 1

    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"unreadable: {exc}"

    try:
        if path.suffix.lower() == ".toml":
            if tomllib is None:
                return None, "parse error: TOML support unavailable"
            return tomllib.loads(raw), None
        return json.loads(raw), None
    except Exception as first_exc:
        if path.suffix.lower() == ".toml":
            return None, f"parse error: {first_exc}"
        try:
            return json.loads(_strip_jsonc(raw)), None
        except Exception as second_exc:
            return None, f"parse error: {second_exc}"


def _looks_like_server_def(value: Any) -> bool:
    return isinstance(value, dict) and bool(_SERVER_HINT_KEYS.intersection(value.keys()))


def _looks_like_server_map(value: Any) -> bool:
    return isinstance(value, dict) and any(_looks_like_server_def(v) for v in value.values())


def _extract_server_maps(data: dict[str, Any]) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []

    for key in _SERVER_MAP_KEYS:
        candidate = data.get(key)
        if _looks_like_server_map(candidate):
            maps.append(candidate)

    mcp_block = data.get("mcp")
    if isinstance(mcp_block, dict):
        for key in _SERVER_MAP_KEYS:
            candidate = mcp_block.get(key)
            if _looks_like_server_map(candidate):
                maps.append(candidate)

    if not maps and _looks_like_server_map(data):
        maps.append(data)

    return maps


def _redact_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _normalize_server(
    name: str,
    raw: dict[str, Any],
    *,
    config_path: Path,
    artha_dir: Path,
    home_dir: Path,
) -> dict[str, Any]:
    type_hint = str(raw.get("type") or raw.get("transport") or "").lower()
    url = raw.get("url") or raw.get("endpoint")
    command = raw.get("command")

    if isinstance(url, str) and url:
        transport = "http" if type_hint in {"", "http", "streamable-http"} else type_hint
    elif isinstance(command, str) and command:
        transport = "stdio"
    else:
        transport = type_hint or "unknown"

    server: dict[str, Any] = {
        "name": str(name),
        "transport": transport,
        "scope": _scope_for(config_path, artha_dir),
        "provider": _provider_for(config_path),
        "config_path": _display_path(config_path, artha_dir, home_dir),
    }
    if isinstance(command, str) and command:
        server["command"] = Path(command).name
    if isinstance(url, str) and url:
        server["url"] = _redact_url(url)
    if isinstance(raw.get("description"), str):
        server["description"] = raw["description"][:240]
    if raw.get("disabled") is True or raw.get("enabled") is False:
        server["enabled"] = False
    return server


def _default_config_paths(artha_dir: Path, home_dir: Path) -> list[Path]:
    return [
        artha_dir / ".vscode" / "mcp.json",
        artha_dir / ".mcp.json",
        artha_dir / ".cursor" / "mcp.json",
        artha_dir / ".warp" / "mcp.json",
        artha_dir / ".warp" / ".mcp.json",
        artha_dir / ".claude" / "mcp.json",
        artha_dir / ".claude.json",
        home_dir / ".mcp.json",
        home_dir / ".config" / "mcp" / "config.json",
        home_dir / ".cursor" / "mcp.json",
        home_dir / ".warp" / "mcp.json",
        home_dir / ".warp" / ".mcp.json",
        home_dir / ".claude.json",
        home_dir / ".config" / "Claude" / "claude_desktop_config.json",
        home_dir / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        home_dir / ".codex" / "config.toml",
    ]


def discover_mcp(
    artha_dir: Path | None = None,
    *,
    home_dir: Path | None = None,
    config_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Return a passive MCP inventory.

    ``config_paths`` is mainly for tests. Missing files are ignored; malformed
    files are included with an error so health checks can surface the problem.
    """
    artha_dir = (artha_dir or Path(__file__).resolve().parents[1]).expanduser().resolve()
    home_dir = (home_dir or Path.home()).expanduser().resolve()
    paths = config_paths or _default_config_paths(artha_dir, home_dir)

    configs: list[dict[str, Any]] = []
    servers: list[dict[str, Any]] = []
    seen: set[Path] = set()

    for raw_path in paths:
        path = raw_path.expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)

        config_entry: dict[str, Any] = {
            "path": _display_path(path, artha_dir, home_dir),
            "scope": _scope_for(path, artha_dir),
            "provider": _provider_for(path),
            "servers": [],
        }
        errors: list[str] = []

        try:
            size = path.stat().st_size
        except OSError as exc:
            size = 0
            errors.append(f"stat error: {exc}")

        data: dict[str, Any] | None = None
        if size > MAX_CONFIG_BYTES:
            errors.append(f"skipped: file is larger than {MAX_CONFIG_BYTES} bytes")
        elif not errors:
            loaded, error = _load_config(path)
            if error:
                errors.append(error)
            elif isinstance(loaded, dict):
                data = loaded
            else:
                errors.append("parse error: root is not an object")

        if data is not None:
            for server_map in _extract_server_maps(data):
                for name, raw_server in sorted(server_map.items()):
                    if not isinstance(raw_server, dict):
                        continue
                    normalized = _normalize_server(
                        str(name),
                        raw_server,
                        config_path=path,
                        artha_dir=artha_dir,
                        home_dir=home_dir,
                    )
                    config_entry["servers"].append(normalized)
                    servers.append(normalized)

        if errors:
            config_entry["errors"] = errors
        configs.append(config_entry)

    transport_counts: dict[str, int] = {}
    for server in servers:
        transport = str(server.get("transport") or "unknown")
        transport_counts[transport] = transport_counts.get(transport, 0) + 1

    errors_count = sum(len(config.get("errors", [])) for config in configs)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "source": "passive_config_scan",
        "counts": {
            "config_files": len(configs),
            "servers": len(servers),
            "project_servers": sum(1 for s in servers if s.get("scope") == "project"),
            "home_servers": sum(1 for s in servers if s.get("scope") == "home"),
            "errors": errors_count,
            "transports": transport_counts,
        },
        "configs": configs,
        "servers": servers,
    }


def summary_dict(discovery: dict[str, Any]) -> dict[str, Any]:
    counts = discovery.get("counts", {})
    return {
        "config_files": counts.get("config_files", 0),
        "servers": counts.get("servers", 0),
        "project_servers": counts.get("project_servers", 0),
        "home_servers": counts.get("home_servers", 0),
        "errors": counts.get("errors", 0),
        "transports": counts.get("transports", {}),
    }


def summary_text(discovery: dict[str, Any]) -> str:
    summary = summary_dict(discovery)
    return (
        f"MCP discovery: {summary['servers']} server(s) in "
        f"{summary['config_files']} config file(s) "
        f"({summary['project_servers']} project, {summary['home_servers']} home, "
        f"{summary['errors']} warning(s))"
    )


def write_discovery(discovery: dict[str, Any], output: Path | None = None) -> Path:
    output = output or (Path(__file__).resolve().parents[1] / DEFAULT_OUTPUT)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(discovery, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Passively inventory MCP client configs")
    parser.add_argument("--artha-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--home-dir", type=Path, default=Path.home())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print full JSON inventory")
    parser.add_argument("--summary-json", action="store_true", help="print compact JSON summary")
    parser.add_argument("--no-write", action="store_true", help="do not write tmp/mcp_discovery.json")
    args = parser.parse_args(argv)

    discovery = discover_mcp(args.artha_dir, home_dir=args.home_dir)
    if not args.no_write:
        write_discovery(discovery, args.output or (args.artha_dir / DEFAULT_OUTPUT))

    if args.summary_json:
        print(json.dumps(summary_dict(discovery), sort_keys=True))
    elif args.json:
        print(json.dumps(discovery, indent=2, sort_keys=True))
    else:
        print(summary_text(discovery))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
