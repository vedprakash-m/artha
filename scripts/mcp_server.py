#!/usr/bin/env python3
# pii-guard: ignore-file — MCP server adapter; string literals are code, not real data
"""
scripts/mcp_server.py — Artha MCP Server

Transport:  stdio (local-only, never HTTP/SSE to remote hosts)
Protocol:   MCP SDK 1.x (FastMCP)

Security invariants (§3.9):
  1. Local-only transport — stdio only; FastMCP called with transport="stdio"
  2. PII guard on all artha_read_state responses (configurable, default True)
  3. Legacy effect tools are inert previews; they never enqueue or execute
  4. No raw vault key or token exposure in params or responses
  5. Audit logging — every invocation appended to state/audit.md
  6. Handler path validation — only connectors.* modules accepted in fetch pipeline
  7. Legacy State writes are not registered as MCP tools

Tools — Phase F1 (read-only):
  artha_fetch_data      Fetch from enabled connectors, returns structured records
  artha_health_check    Run connector health checks
  artha_read_state      Read a domain state file (auto-decrypt .age, PII-guard)
  artha_pii_scan        Scan text for PII patterns without modifying it
  artha_preflight       Run pre-catch-up health gate
  artha_list_connectors List configured connectors from connectors.yaml
  artha_run_skills      Fixed denial until each skill has a reviewed capability

Tools — Phase F2 (inert proposals):
  artha_send_email      Return a local preview only
  artha_todo_sync       Return a local preview only

Ref: supercharge-reloaded.md §3.4–3.11
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure scripts/ is on sys.path for sibling imports
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# MCP SDK
# ---------------------------------------------------------------------------
from mcp.server.fastmcp import FastMCP  # type: ignore[import]

mcp = FastMCP("artha")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_STATE_DIR = _REPO_ROOT / "state"
from lib.local_paths import audit_log_path
_AUDIT_LOG = audit_log_path(_STATE_DIR.parent)

# Domains stored as encrypted .age files (contain sensitive PII)
_ENCRYPTED_DOMAINS: frozenset[str] = frozenset({
    "audit", "contacts", "estate", "finance", "health",
    "immigration", "insurance", "occasions", "vehicle",
})

# Only connector modules from the connectors package are allowed (§3.9.6)
_CONNECTOR_PKG = "connectors"

import logging
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiting — stdlib token-bucket implementation (§3.9 hardening)
# ---------------------------------------------------------------------------
import time as _time
import threading as _threading

class _RateLimiter:
    """Simple token-bucket rate limiter. Thread-safe."""
    def __init__(self, max_calls: int, period_seconds: float):
        self._max = max_calls
        self._period = period_seconds
        self._calls: list[float] = []
        self._lock = _threading.Lock()

    def allow(self) -> bool:
        now = _time.monotonic()
        with self._lock:
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max:
                return False
            self._calls.append(now)
            return True

_READ_LIMITER = _RateLimiter(max_calls=30, period_seconds=60)
_WRITE_LIMITER = _RateLimiter(max_calls=10, period_seconds=60)

def _check_rate_limit(tool: str, is_write: bool = False) -> str | None:
    """Return error JSON if rate-limited; None to proceed."""
    limiter = _WRITE_LIMITER if is_write else _READ_LIMITER
    if not limiter.allow():
        _audit(tool, {}, "rejected:rate_limited")
        return json.dumps({
            "error": "Rate limit exceeded. Try again shortly.",
            "tool": tool,
        })
    return None


# ---------------------------------------------------------------------------
# Audit logging (§3.9.5) — PII-scrubbed, never exposes keys/tokens
# ---------------------------------------------------------------------------
def _audit(tool: str, params: dict[str, Any], status: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # Substring matches for common secret patterns (e.g., 'api_key', 'oauth_token')
    _SENSITIVE_SUB = {"key", "password", "token", "secret"}
    # Exact matches for PII fields to avoid over-masking (e.g., 'to' shouldn't mask 'total')
    _SENSITIVE_EXACT = {"to", "email", "phone", "address", "ssn", "content"}

    safe = {
        k: "***" if (any(s in k.lower() for s in _SENSITIVE_SUB) or k.lower() in _SENSITIVE_EXACT) else v
        for k, v in params.items()
    }
    entry = (
        f"[{ts}] MCP | tool={tool} | "
        f"params={json.dumps(safe, default=str)} | status={status}\n"
    )
    try:
        with open(_AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError as exc:
        _log.warning("audit_write_failed error=%s", exc)
        pass  # audit log not writable; don't crash the server


# ---------------------------------------------------------------------------
# Vault helpers — read/write state files with optional age encryption
# ---------------------------------------------------------------------------
def _read_state_file(domain: str) -> str:
    """Read state/{domain}.md, auto-decrypting .age if needed."""
    domain = domain.removesuffix(".md")
    plain = _STATE_DIR / f"{domain}.md"
    encrypted = _STATE_DIR / f"{domain}.md.age"

    if plain.exists():
        return plain.read_text(encoding="utf-8")

    if encrypted.exists():
        import vault  # type: ignore[import]
        privkey = vault.get_private_key()
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            ok = vault.age_decrypt(privkey, encrypted, tmp_path)
            if not ok:
                raise RuntimeError(f"age decrypt failed for {domain}.md.age")
            return tmp_path.read_text(encoding="utf-8")
        finally:
            tmp_path.unlink(missing_ok=True)

    raise FileNotFoundError(f"No state file found for domain '{domain}'")


def _write_state_file(domain: str, content: str) -> bool:
    """Write content to state/{domain}.md. Returns True if encrypted afterwards."""
    domain = domain.removesuffix(".md")
    plain = _STATE_DIR / f"{domain}.md"
    plain.write_text(content, encoding="utf-8")

    if domain not in _ENCRYPTED_DOMAINS:
        return False

    import vault  # type: ignore[import]
    pubkey = vault.get_public_key()
    encrypted = _STATE_DIR / f"{domain}.md.age"
    ok = vault.age_encrypt(pubkey, plain, encrypted)
    if not ok:
        raise RuntimeError(f"age encrypt failed for domain '{domain}'")
    plain.unlink()
    return True


def _known_domains() -> frozenset[str]:
    """Derive valid domain names from files currently present in state/."""
    domains: set[str] = set()
    for p in _STATE_DIR.iterdir():
        if p.name.endswith(".md.age"):
            domains.add(p.name[: -len(".md.age")])
        elif p.name.endswith(".md"):
            domains.add(p.name[: -len(".md")])
    return frozenset(domains)


# ---------------------------------------------------------------------------
# Pipeline helpers — collect records in-memory (no stdout capture)
# ---------------------------------------------------------------------------
def _fetch_in_memory(
    since: str,
    source_filter: list[str] | None,
    max_results: int,
) -> list[dict]:
    """Run the connector pipeline, collecting records into a list."""
    from pipeline import (  # type: ignore[import]
        load_connectors_config,
        _enabled_connectors,
        _load_handler,
    )
    from lib.auth import load_auth_context  # type: ignore[import]
    from lib.retry import with_retry  # type: ignore[import]

    cfg = load_connectors_config()
    connectors = _enabled_connectors(cfg, source_filter)
    records: list[dict] = []

    for conn in connectors:
        if len(records) >= max_results:
            break

        name = conn["name"]
        handler_path = conn.get("fetch", {}).get("handler", "")
        if not handler_path:
            continue

        # §3.9.6 — only allow modules from the connectors package
        # Accept "scripts/connectors/x.py" (filesystem) or "connectors.x" (module)
        if "/" in handler_path:
            path_parts = handler_path.replace("\\", "/").split("/")
            if _CONNECTOR_PKG not in path_parts:
                continue
        else:
            if not handler_path.startswith(_CONNECTOR_PKG + "."):
                continue

        try:
            handler = _load_handler(handler_path)
            auth_ctx = load_auth_context(conn)
        except Exception as exc:
            _log.warning("connector_load_failed name=%s error=%s", name, exc)
            continue

        fetch_cfg = conn.get("fetch", {})
        extra_kw: dict[str, Any] = {
            k: v for k, v in fetch_cfg.items() if k not in ("handler", "max_results")
        }
        cap = min(
            max_results - len(records),
            fetch_cfg.get("max_results", max_results),
        )
        retry_cfg = conn.get("retry", {})

        def _do(
            _h=handler, _s=since, _m=cap,
            _a=auth_ctx, _t=name, _kw=extra_kw,
        ) -> list[dict]:
            return list(_h.fetch(since=_s, max_results=_m, auth_context=_a,
                                 source_tag=_t, **_kw))

        try:
            batch: list[dict] = with_retry(
                _do,
                max_retries=retry_cfg.get("max_attempts", 3),
                base_delay=retry_cfg.get("base_delay_seconds", 1.0),
                backoff_mult=retry_cfg.get("backoff_multiplier", 2.0),
                max_delay=retry_cfg.get("max_delay_seconds", 30.0),
                context=f"mcp.{name}",
                label=name,
            )
            records.extend(batch)
        except Exception as exc:
            _log.warning("connector_fetch_failed name=%s error=%s", name, exc)
            continue

    return records


# ===========================================================================
# Phase F1 — Read-only tools
# ===========================================================================

@mcp.tool()
def artha_fetch_data(
    since: str,
    source: str | None = None,
    max_results: int = 200,
) -> str:
    """Return a fixed denial until trusted personal source classification is available."""
    return json.dumps({"error": "personal source classification unavailable"})


@mcp.tool()
def artha_health_check(source: str | None = None) -> str:
    """Run connector health checks.

    Args:
        source: Optional connector name. If omitted, checks all enabled connectors.

    Returns JSON object mapping connector name → {healthy: bool, error?: str}.
    """
    rejection = _check_rate_limit("artha_health_check")
    if rejection:
        return rejection
    params: dict[str, Any] = {"source": source}
    try:
        from pipeline import (  # type: ignore[import]
            load_connectors_config,
            _enabled_connectors,
            _load_handler,
        )
        from lib.auth import load_auth_context  # type: ignore[import]

        cfg = load_connectors_config()
        connectors = _enabled_connectors(cfg, [source] if source else None)
        out: dict[str, Any] = {}

        for conn in connectors:
            name = conn["name"]
            handler_path = (
                conn.get("health_check", {}).get("handler")
                or conn.get("fetch", {}).get("handler", "")
            )
            if not handler_path:
                out[name] = {"healthy": None, "error": "no handler defined"}
                continue
            try:
                handler = _load_handler(handler_path)
                auth_ctx = load_auth_context(conn)
                ok = handler.health_check(auth_ctx)
                out[name] = {"healthy": bool(ok)}
            except Exception:
                out[name] = {"healthy": False, "error": "connector health check failed"}

        _audit("artha_health_check", params, "ok")
        return json.dumps(out, ensure_ascii=False)
    except Exception:
        _audit("artha_health_check", params, "error")
        return json.dumps({"error": "connector health checks unavailable"})


@mcp.tool()
def artha_read_state(domain: str, pii_redact: bool = True) -> str:
    """Return a fixed denial until trusted personal State classification is available."""
    return json.dumps({"error": "personal State classification unavailable"})


@mcp.tool()
def artha_pii_scan(text: str) -> str:
    """Scan text for PII patterns without modifying it.

    Args:
        text: Text to scan.

    Returns JSON: {"found": bool, "types": {type_name: count}}.
    """
    rejection = _check_rate_limit("artha_pii_scan")
    if rejection:
        return rejection
    params: dict[str, Any] = {"text_length": len(text)}
    if len(text) > 100_000:
        return json.dumps({"error": "input exceeds the configured limit"})
    try:
        from pii_guard import scan  # type: ignore[import]
        found, type_counts = scan(text)
        _audit("artha_pii_scan", params, f"ok:found={found}")
        return json.dumps({"found": found, "types": type_counts})
    except Exception:
        _audit("artha_pii_scan", params, "error")
        return json.dumps({"error": "PII scanner unavailable"})


@mcp.tool()
def artha_preflight(quiet: bool = True) -> str:
    """Run pre-catch-up health gate checks.

    Args:
        quiet: Skip live API connectivity checks for faster results (default True).

    Returns JSON array of check results:
        [{name, severity, passed, message, fix_hint?}]
    """
    rejection = _check_rate_limit("artha_preflight")
    if rejection:
        return rejection
    params: dict[str, Any] = {"quiet": quiet}
    try:
        from preflight import run_preflight  # type: ignore[import]
        results = run_preflight(quiet=quiet)
        out = []
        for r in results:
            item: dict[str, Any] = {
                "name": r.name,
                "severity": r.severity,
                "passed": r.passed,
                "message": r.message,
            }
            if r.fix_hint:
                item["fix_hint"] = r.fix_hint
            out.append(item)
        failed_count = sum(1 for r in results if not r.passed)
        _audit("artha_preflight", params, f"ok:{failed_count}_failed")
        return json.dumps(out, ensure_ascii=False)
    except Exception:
        _audit("artha_preflight", params, "error")
        return json.dumps({"error": "preflight unavailable"})


@mcp.tool()
def artha_list_connectors() -> str:
    """List all configured connectors with name, type, enabled status, and handler.

    Returns JSON array of connector descriptors.
    """
    rejection = _check_rate_limit("artha_list_connectors")
    if rejection:
        return rejection
    try:
        from pipeline import load_connectors_config  # type: ignore[import]
        cfg = load_connectors_config()
        out = []
        for c in cfg.get("connectors", []):
            item: dict[str, Any] = {
                "name": c["name"],
                "type": c.get("type", ""),
                "enabled": c.get("enabled", True),
                "handler": c.get("fetch", {}).get("handler", ""),
            }
            if "mcp" in c:
                item["mcp"] = c["mcp"]
            out.append(item)
        _audit("artha_list_connectors", {}, f"ok:{len(out)}_connectors")
        return json.dumps(out, ensure_ascii=False)
    except Exception:
        _audit("artha_list_connectors", {}, "error")
        return json.dumps({"error": "connector discovery unavailable"})


@mcp.tool()
def artha_run_skills(skill_name: str | None = None) -> str:
    """Return a fixed denial until every skill has a reviewed capability."""
    _audit("artha_run_skills", {"skill_name": skill_name}, "denied:capability_held")
    return json.dumps({"error": "skill execution is held"})


# ===========================================================================
# Phase F2 — inert legacy effect previews (no queue write or execution)
# ===========================================================================

def artha_write_state(
    domain: str,
    content: str,
    approved: bool = False,
) -> str:
    """Return a fixed denial until trusted personal State classification is available."""
    return json.dumps({"error": "personal State classification unavailable"})


@mcp.tool()
def artha_send_email(
    to: str,
    subject: str,
    body: str,
    approved: bool = False,
    archive: bool = False,
) -> str:
    """Return an inert local email preview; MCP never enqueues or sends it.

    Args:
        to:       Recipient email address(es), comma-separated.
        subject:  Subject line.
        body:     Plain-text body.
        approved: Retained only for caller compatibility; it grants no MCP effect.
        archive: Retained in the preview payload; previewing never writes it.

    The owner applies the preview through the ActionQueue CLI review path.
    """
    payload = {"to": to, "subject": subject, "body": body, "archive": archive}
    _audit("artha_send_email", {"to": to, "body_length": len(body), "archive": archive}, "preview")
    return json.dumps({
        "status": "preview",
        "action_type": "email_send",
        "effect": "none",
        "payload": payload,
        "to_apply": "Use the ActionQueue CLI review path; MCP never enqueues or executes this effect.",
    }, ensure_ascii=False)


@mcp.tool()
def artha_todo_sync(
    approved: bool = False,
    dry_run: bool = False,
) -> str:
    """Return an inert To Do sync preview; MCP never enqueues or executes it.

    Args:
        approved: Retained only for caller compatibility; it grants no MCP effect.
        dry_run: Retained in the preview payload; previewing never contacts a provider.

    The owner applies the preview through the ActionQueue CLI review path.
    """
    _audit("artha_todo_sync", {"dry_run": dry_run}, "preview")
    return json.dumps({
        "status": "preview",
        "action_type": "todo_sync",
        "effect": "none",
        "payload": {"dry_run": dry_run},
        "to_apply": "Use the ActionQueue CLI review path; MCP never enqueues or executes this effect.",
    })


# ===========================================================================
# Knowledge Graph tools
# ===========================================================================

@mcp.tool()
def artha_kb_query(
    entity_id: str,
    token_budget: int = 950,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        entity_id: The stable entity ID (e.g. 'infrastructure-armada-platform').
        token_budget: Approximate token limit for the returned context block.
    """
    _audit("artha_kb_query", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_context(
    entity_name: str,
    depth: int = 2,
    token_budget: int = 950,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        entity_name: Human-readable name to search for (case-insensitive).
        depth: BFS hop depth (1–3 recommended).
        token_budget: Approximate token limit for the returned context block.
    """
    _audit("artha_kb_context", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_search(
    query: str,
    domain: str = "",
    limit: int = 10,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        query: Search terms (FTS5 — supports phrase and prefix queries).
        domain: Filter results to this domain (leave empty for all domains).
        limit: Maximum results to return.
    """
    _audit("artha_kb_search", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_recent_changes(
    domain: str = "",
    days: int = 30,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        domain: Filter to a specific domain (empty = all).
        days: Look-back window in days.
    """
    _audit("artha_kb_recent_changes", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_stale(
    domain: str = "",
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        domain: Filter to a specific domain (empty = all).
    """
    _audit("artha_kb_stale", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_global(
    question: str,
    max_tokens: int = 800,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    No question, token budget or result is processed.
    """
    _audit("artha_kb_global", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


@mcp.tool()
def artha_kb_episodes(
    entity_mentions: str = "",
    since_days: int = 30,
) -> str:
    """Return a fixed denial because no authorized MCP viewer is available.

    Args:
        entity_mentions: Optional comma-separated entity IDs to filter by.
        since_days: How many days back to look.
    """
    _audit("artha_kb_episodes", {}, "denied:viewer_unavailable")
    return json.dumps({"error": "knowledge graph read authorization unavailable"})


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    mcp.run(transport="stdio")
