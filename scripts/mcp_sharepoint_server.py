#!/usr/bin/env python3
# pii-guard: ignore-file — MCP server scaffolding; no personal data stored here
"""
scripts/mcp_sharepoint_server.py — Work documents MCP server for Artha.

Reads only from the request-time authorized OneDrive for Business sync root.
Results are display-only local-document views, not ingested or verified evidence.

Exposes three tools to Work OS agents (artha-work-msft, artha-work-enterprise):

  list_recent_documents  — list the N most recently modified work docs
  search_documents       — search by filename and/or content keywords
  read_document          — extract plain text from a local work file

Transport: stdio JSON-RPC 2.0 (MCP protocol).
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Request-time Work authority
# ---------------------------------------------------------------------------

_TEXT_EXTS  = {".txt", ".md", ".csv", ".log", ".py", ".json", ".yaml", ".yml"}
_DOC_EXTS   = {".docx", ".pptx", ".xlsx"}
_OTHER_EXTS = {".pdf", ".one"}
_ALL_EXTS   = _TEXT_EXTS | _DOC_EXTS | _OTHER_EXTS
_SKIP_DIRS  = {".git", "__pycache__", ".tmp", "appdata", "~recycle"}
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_SCAN_BYTES = 64 * 1024 * 1024
_MAX_DISCOVERY_ENTRIES = 100_000
_MAX_LINE = 1_000_000
_MAX_RESULT_CHARS = 120_000
_MAX_TEXT_CHARS = 64_000
_REPARSE_POINT = 0x400
_CLOUD_REPARSE_MASK = 0xFFFF0000
_CLOUD_REPARSE_TAG = 0x90000000
_PROVENANCE = {
    "source": "local-onedrive-commercial",
    "classification": "untrusted-work-display",
    "persistent": False,
}


class _Denied(RuntimeError):
    pass


@dataclass(frozen=True)
class _Authority:
    profile: str
    subject: str
    root: Path
    root_identity: tuple[int, int, int]
    redact_keywords: tuple[str, ...]


def _is_cloud_reparse(metadata: os.stat_result) -> bool:
    return (getattr(metadata, "st_reparse_tag", 0) & _CLOUD_REPARSE_MASK) == _CLOUD_REPARSE_TAG


def _canonical_existing_directory(path: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _Denied from None
    if (not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
            or not resolved.is_dir()):
        raise _Denied
    return resolved


def _configured_profile() -> str:
    for value in (os.environ.get("ARTHA_WORK_PROFILE"), os.environ.get("ARTHA_PROFILE")):
        if value is not None and value != "work":
            raise _Denied
    return "work"


def _configured_subject() -> str:
    if os.name == "nt":
        native = os.environ.get("USERNAME") or ""
    else:
        native = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if os.environ.get("USER") and os.environ.get("LOGNAME") and os.environ["USER"] != os.environ["LOGNAME"]:
            raise _Denied
    configured = os.environ.get("ARTHA_WORK_SUBJECT")
    if configured is not None and configured != native:
        raise _Denied
    if (not native or native != native.strip() or len(native) > 256
            or any(ord(char) < 32 for char in native)):
        raise _Denied
    return native


def _find_work_onedrive() -> Path | None:
    """Resolve the exact commercial root; never use generic OneDrive fallback."""
    raw = os.environ.get("OneDriveCommercial", "")
    if (not raw or raw != raw.strip() or "\n" in raw or "\r" in raw
            or os.pathsep in raw):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        return None
    try:
        commercial = _canonical_existing_directory(path)
    except _Denied:
        return None

    personal = {
        os.environ.get("OneDrive", ""),
        os.environ.get("OneDriveConsumer", ""),
    }
    for value in personal:
        if not value:
            continue
        try:
            if Path(value).expanduser().resolve(strict=True) == commercial:
                return None
        except (OSError, RuntimeError):
            continue

    return commercial


def _load_work_redaction_policy() -> tuple[str, ...]:
    """Load the Work redaction policy without a fail-open fallback."""
    try:
        from lib.config_loader import load_config  # noqa: PLC0415

        config = load_config("user_profile", strict=True)
        privacy = config.get("privacy") if isinstance(config, dict) else None
        keywords = privacy.get("redact_keywords") if isinstance(privacy, dict) else None
        if not isinstance(keywords, list):
            raise _Denied
        clean: list[str] = []
        for keyword in keywords:
            if (not isinstance(keyword, str) or not keyword or keyword != keyword.strip()
                    or len(keyword) > 256 or any(ord(char) < 32 for char in keyword)):
                raise _Denied
            clean.append(keyword)
        return tuple(dict.fromkeys(clean))
    except _Denied:
        raise
    except Exception:
        raise _Denied from None


def _authority() -> _Authority:
    try:
        root = _find_work_onedrive()
        if root is None:
            raise _Denied
        root = _canonical_existing_directory(root)
        root_info = root.lstat()
        return _Authority(
            profile=_configured_profile(),
            subject=_configured_subject(),
            root=root,
            root_identity=(root_info.st_dev, root_info.st_ino, root_info.st_mode),
            redact_keywords=_load_work_redaction_policy(),
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _Denied from None


def _refusal() -> dict[str, str]:
    return {"error": "Work document read authorization unavailable"}


def _bounded_integer(args: dict[str, Any], name: str, default: int, maximum: int) -> int:
    value = args.get(name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 to {maximum}")
    return value


def _strict_args(args: Any, allowed: set[str]) -> dict[str, Any]:
    if (not isinstance(args, dict) or set(args) - allowed
            or any(not isinstance(key, str) for key in args)):
        raise ValueError("invalid arguments")
    return args


def _extension_filter(args: dict[str, Any]) -> set[str]:
    raw = args.get("extensions", [])
    if raw in (None, []):
        return set(_ALL_EXTS)
    if not isinstance(raw, list) or not 1 <= len(raw) <= len(_ALL_EXTS):
        raise ValueError("extensions must be a bounded list")
    clean: set[str] = set()
    for value in raw:
        if not isinstance(value, str) or not value.startswith(".") or len(value) > 16:
            raise ValueError("invalid extension filter")
        extension = value.lower()
        if extension not in _ALL_EXTS:
            raise ValueError("unsupported extension filter")
        clean.add(extension)
    return clean


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(keyword.casefold() in folded for keyword in keywords)


def _redact(text: str, keywords: tuple[str, ...]) -> str:
    for keyword in keywords:
        text = re.sub(re.escape(keyword), "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def _authority_fields(authority: _Authority) -> dict[str, Any]:
    return {
        "profile": authority.profile,
        "subject": authority.subject,
        "displayOnly": True,
        "provenance": dict(_PROVENANCE),
    }


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_text_from_bytes(path: Path, data: bytes, max_chars: int = 10_000) -> str:
    ext = path.suffix.lower()
    if ext in _TEXT_EXTS:
        return data.decode("utf-8", errors="replace")[:max_chars]
    if ext == ".docx":
        try:
            import docx

            doc = docx.Document(BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:max_chars]
        except ImportError:
            return "[python-docx not installed — pip install python-docx]"
        except Exception:
            return "[Could not extract .docx text]"
    if ext == ".pptx":
        try:
            from pptx import Presentation

            prs = Presentation(BytesIO(data))
            parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        parts.append(shape.text.strip())
            return "\n".join(parts)[:max_chars]
        except ImportError:
            return "[python-pptx not installed — pip install python-pptx]"
        except Exception:
            return "[Could not extract .pptx text]"
    if ext == ".xlsx":
        try:
            import openpyxl

            wb = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    line = "\t".join(str(v) for v in row if v is not None)
                    if line.strip():
                        rows.append(line)
            return "\n".join(rows)[:max_chars]
        except ImportError:
            return "[openpyxl not installed — pip install openpyxl]"
        except Exception:
            return "[Could not extract .xlsx text]"
    if ext == ".pdf":
        try:
            import pypdf

            reader = pypdf.PdfReader(BytesIO(data))
            parts: list[str] = []
            size = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                parts.append(text[:max(0, max_chars - size)])
                size += len(parts[-1])
                if size >= max_chars:
                    break
            return "".join(parts)
        except ImportError:
            return "[pypdf not installed — pip install pypdf]"
        except Exception:
            return "[Could not extract .pdf text]"
    return f"[Binary file — {path.suffix} — text extraction not supported]"


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _authorized_file(authority: _Authority, path: Path) -> tuple[Path, os.stat_result]:
    if not path.is_absolute():
        raise _Denied
    root = authority.root
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise _Denied from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _Denied

    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            raise _Denied from None
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT):
            raise _Denied
    try:
        info = candidate.lstat()
    except OSError:
        raise _Denied from None
    attributes = getattr(info, "st_file_attributes", 0)
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or (attributes & _REPARSE_POINT and not _is_cloud_reparse(info))):
        raise _Denied
    try:
        root_info = root.lstat()
    except OSError:
        raise _Denied from None
    if (_canonical_existing_directory(root) != root
            or (root_info.st_dev, root_info.st_ino, root_info.st_mode) != authority.root_identity):
        raise _Denied
    return candidate, info


def _read_authorized_file(authority: _Authority, path: Path) -> tuple[Path, bytes, os.stat_result]:
    candidate, before = _authorized_file(authority, path)
    if before.st_size > _MAX_FILE_BYTES:
        raise _Denied
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or _file_identity(opened) != _file_identity(before)
                    or opened.st_size > _MAX_FILE_BYTES):
                raise _Denied
            data = handle.read(_MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        current = candidate.lstat()
    except (OSError, ValueError):
        raise _Denied from None
    if (len(data) > _MAX_FILE_BYTES or len(data) != opened.st_size
            or _file_identity(after) != _file_identity(before)
            or _file_identity(current) != _file_identity(before)):
        raise _Denied
    return candidate, data, opened


def _file_meta(authority: _Authority, path: Path, info: os.stat_result) -> dict[str, Any]:
    if _has_keyword(str(path.relative_to(authority.root)), authority.redact_keywords):
        raise _Denied
    return {
        "name":         path.name,
        "path":         str(path),
        "relativePath": path.relative_to(authority.root).as_posix(),
        "ext":          path.suffix.lower(),
        "sizeBytes":    info.st_size,
        "modified":     datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat(),
    }


def _documents(authority: _Authority, extensions: set[str]):
    entries = 0
    try:
        for path in authority.root.rglob("*"):
            entries += 1
            if entries > _MAX_DISCOVERY_ENTRIES:
                raise _Denied
            if any(part.casefold() in _SKIP_DIRS for part in path.relative_to(authority.root).parts):
                continue
            try:
                candidate, info = _authorized_file(authority, path)
            except _Denied:
                continue
            if candidate.suffix.lower() in extensions and not _has_keyword(
                    str(candidate.relative_to(authority.root)), authority.redact_keywords):
                yield candidate, info
    except _Denied:
        raise
    except (OSError, RuntimeError):
        raise _Denied from None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_list_recent_documents(args: dict) -> dict:
    try:
        args = _strict_args(args, {"limit", "extensions"})
        limit = _bounded_integer(args, "limit", 20, 100)
        extensions = _extension_filter(args)
        authority = _authority()
        files = list(_documents(authority, extensions))
        files.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
        results = [_file_meta(authority, path, info) for path, info in files[:limit]]
        return {
            **_authority_fields(authority),
            "files": results,
            "count": len(results),
            "root": str(authority.root),
        }
    except (_Denied, OSError, RuntimeError, TypeError, ValueError):
        return _refusal()


def _tool_search_documents(args: dict) -> dict:
    try:
        args = _strict_args(args, {"query", "limit", "content_search", "extensions"})
        query = args.get("query")
        if (not isinstance(query, str) or not query.strip() or query != query.strip()
                or len(query) > 512 or any(ord(char) < 32 for char in query)):
            raise ValueError("invalid query")
        limit = _bounded_integer(args, "limit", 15, 50)
        content_search = args.get("content_search", True)
        if type(content_search) is not bool:
            raise ValueError("content_search must be a boolean")
        extensions = _extension_filter(args)
        authority = _authority()
        terms = [term.casefold() for term in query.split() if term]
        if not terms:
            raise ValueError("invalid query")

        hits: list[dict[str, Any]] = []
        scanned_bytes = 0
        for path, info in _documents(authority, extensions):
            name_match = all(term in path.name.casefold() for term in terms)
            content_match = False
            matched_lines: list[str] = []
            if not name_match and content_search and path.suffix.lower() in _TEXT_EXTS:
                if info.st_size > _MAX_FILE_BYTES:
                    raise _Denied
                path, data, info = _read_authorized_file(authority, path)
                scanned_bytes += len(data)
                if scanned_bytes > _MAX_SCAN_BYTES:
                    raise _Denied
                text = data.decode("utf-8", errors="replace")
                if all(term in text.casefold() for term in terms):
                    content_match = True
                    for line in text.splitlines():
                        if any(term in line.casefold() for term in terms):
                            stripped = line.strip()
                            if stripped:
                                matched_lines.append(_redact(stripped[:200], authority.redact_keywords))
                            if len(matched_lines) >= 3:
                                break
            if name_match or content_match:
                meta = _file_meta(authority, path, info)
                meta["matchType"] = "filename" if name_match else "content"
                if matched_lines:
                    meta["snippets"] = matched_lines
                hits.append(meta)
        fn_hits = sorted(
            [hit for hit in hits if hit["matchType"] == "filename"],
            key=lambda hit: hit["modified"],
            reverse=True,
        )
        content_hits = sorted(
            [hit for hit in hits if hit["matchType"] == "content"],
            key=lambda hit: hit["modified"],
            reverse=True,
        )
        results = (fn_hits + content_hits)[:limit]
        return {
            **_authority_fields(authority),
            "results": results,
            "count": len(results),
            "query": _redact(query, authority.redact_keywords),
            "root": str(authority.root),
        }
    except (_Denied, OSError, RuntimeError, TypeError, ValueError):
        return _refusal()


def _tool_read_document(args: dict) -> dict:
    try:
        args = _strict_args(args, {"path", "max_chars"})
        file_path = args.get("path")
        if (not isinstance(file_path, str) or not file_path or len(file_path) > 4096
                or any(ord(char) < 32 for char in file_path)):
            raise ValueError("invalid path")
        path = Path(file_path)
        if not path.is_absolute():
            raise _Denied
        max_chars = _bounded_integer(args, "max_chars", 8000, _MAX_TEXT_CHARS)
        authority = _authority()
        if _has_keyword(str(path), authority.redact_keywords):
            raise _Denied
        path, data, _ = _read_authorized_file(authority, path)
        if path.suffix.lower() not in _ALL_EXTS:
            raise _Denied
        text = _redact(_extract_text_from_bytes(path, data, max_chars), authority.redact_keywords)
        return {
            **_authority_fields(authority),
            "name":      path.name,
            "path":      str(path),
            "root":      str(authority.root),
            "ext":       path.suffix.lower(),
            "text":      text,
            "truncated": len(text) >= max_chars,
            "chars":     len(text),
        }
    except (_Denied, OSError, RuntimeError, TypeError, ValueError):
        return _refusal()


# ---------------------------------------------------------------------------
# MCP protocol (stdio JSON-RPC 2.0)
# ---------------------------------------------------------------------------

_TOOLS = {
    "list_recent_documents": {
        "description": (
            "List recently modified documents from the request-authorized local "
            "OneDrive for Business root. Results are Work-only display metadata."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Max files to return (default 20, max 100).",
                    "default": 20,
                },
                "extensions": {
                    "type": "array",
                    "maxItems": len(_ALL_EXTS),
                    "items": {"type": "string", "maxLength": 16},
                    "description": "Filter by supported extensions, for example [\".docx\",\".pdf\"].",
                },
            },
            "additionalProperties": False,
        },
    },
    "search_documents": {
        "description": (
            "Search document names and bounded text under the request-authorized "
            "OneDrive for Business root. Results are Work-only display views."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":          {"type": "string", "minLength": 1, "maxLength": 512, "description": "Keywords to search (all must match)."},
                "limit":          {"type": "integer", "minimum": 1, "maximum": 50, "description": "Max results (default 15, max 50).", "default": 15},
                "content_search": {"type": "boolean", "description": "Also search inside text/md files (default true).", "default": True},
                "extensions":     {"type": "array", "maxItems": len(_ALL_EXTS), "items": {"type": "string", "maxLength": 16}, "description": "Filter by supported extensions."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "read_document": {
        "description": (
            "Extract a bounded display-only view of a locally authorized Work document. "
            "Supports .txt, .md, .csv, .docx, .pptx, .xlsx, .pdf. "
            "Use a path from search_documents or list_recent_documents; content is "
            "not persisted or promoted to verified evidence."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "path":      {"type": "string", "maxLength": 4096, "description": "Absolute local path returned by a discovery tool."},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": _MAX_TEXT_CHARS, "description": "Truncate at this many chars (default 8000).", "default": 8000},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

_TOOL_HANDLERS = {
    "list_recent_documents": _tool_list_recent_documents,
    "search_documents":      _tool_search_documents,
    "read_document":         _tool_read_document,
}


def _handle(request: dict) -> dict | None:
    if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)
            or ("id" in request and (isinstance(request["id"], bool)
                                    or not isinstance(request["id"], (str, int, type(None)))))):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        }
    method = request["method"]
    req_id = request.get("id")
    if "id" not in request:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "artha-work-docs", "version": "2.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{"name": n, **d} for n, d in _TOOLS.items()]}}
    if method == "tools/call":
        params = request.get("params")
        if (not isinstance(params, dict) or not isinstance(params.get("name"), str)
                or params["name"] not in _TOOL_HANDLERS):
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Unknown tool"}}
        handler = _TOOL_HANDLERS[params["name"]]
        try:
            result = handler(params.get("arguments", {}))
            text = json.dumps(result, indent=2)
            if len(text) > _MAX_RESULT_CHARS:
                result = {"error": "result exceeds the configured limit"}
                text = json.dumps(result, indent=2)
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": "error" in result,
                },
            }
        except Exception:
            text = json.dumps({"error": "request could not be completed"}, indent=2)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return None


def serve(stdin, stdout) -> int:
    while raw := stdin.readline(_MAX_LINE + 1):
        if len(raw) > _MAX_LINE:
            while raw and not raw.endswith("\n"):
                raw = stdin.readline(_MAX_LINE + 1)
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Request too large"},
            }
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = _handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


def main() -> None:
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
