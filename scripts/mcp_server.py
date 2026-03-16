#!/usr/bin/env python3
# pii-guard: ignore-file — MCP server adapter; string literals are code, not real data
"""
scripts/mcp_server.py — Artha MCP Server

Transport:  stdio (local-only, never HTTP/SSE to remote hosts)
Protocol:   MCP SDK 1.x (FastMCP)

Security invariants (§3.9):
  1. Local-only transport — stdio only; FastMCP called with transport="stdio"
  2. PII guard on all artha_read_state responses (configurable, default True)
  3. Write tools require approved=True — rejected immediately if False/missing
  4. No raw vault key or token exposure in params or responses
  5. Audit logging — every invocation appended to state/audit.md
  6. Handler path validation — only connectors.* modules accepted in fetch pipeline
  7. Domain validation — artha_write_state only accepts known state/ domains

Tools — Phase F1 (read-only):
  artha_fetch_data      Fetch from enabled connectors, returns structured records
  artha_health_check    Run connector health checks
  artha_read_state      Read a domain state file (auto-decrypt .age, PII-guard)
  artha_pii_scan        Scan text for PII patterns without modifying it
  artha_preflight       Run pre-catch-up health gate
  artha_list_connectors List configured connectors from connectors.yaml
  artha_run_skills      Run data fidelity skills via skill_runner

Tools — Phase F2 (write, require approved=True):
  artha_write_state     Write to a domain state file (auto-encrypt sensitive domains)
  artha_send_email      Send email via Gmail API
  artha_todo_sync       Sync open_items.md ↔ Microsoft To Do

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
_AUDIT_LOG = _STATE_DIR / "audit.md"

# Domains stored as encrypted .age files (contain sensitive PII)
_ENCRYPTED_DOMAINS: frozenset[str] = frozenset({
    "audit", "contacts", "estate", "finance", "health",
    "immigration", "insurance", "occasions", "vehicle",
})

# Only connector modules from the connectors package are allowed (§3.9.6)
_CONNECTOR_PKG = "connectors"


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
    _SENSITIVE = {"key", "password", "token", "secret", "content"}
    safe = {
        k: "***" if any(s in k.lower() for s in _SENSITIVE) else v
        for k, v in params.items()
    }
    entry = (
        f"[{ts}] MCP | tool={tool} | "
        f"params={json.dumps(safe, default=str)} | status={status}\n"
    )
    try:
        with open(_AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except OSError:
        pass  # audit log not writable; don't crash the server


# ---------------------------------------------------------------------------
# Write-gate (§3.9.3) — all write tools call this first
# ---------------------------------------------------------------------------
def _require_approval(approved: bool, tool: str, params: dict[str, Any]) -> str | None:
    """Return error JSON if not approved or rate-limited; None to proceed."""
    rl = _check_rate_limit(tool, is_write=True)
    if rl:
        return rl
    if not approved:
        _audit(tool, params, "rejected:approval_required")
        return json.dumps({
            "error": "Write operation requires approved=True.",
            "tool": tool,
        })
    return None


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
        except Exception:
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
        except Exception:
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
    """Fetch email/calendar/LMS data from enabled connectors.

    Args:
        since:       ISO-8601 start timestamp, e.g. "2026-03-01T00:00:00Z"
        source:      Optional connector name (e.g. "gmail", "outlook_calendar").
                     If omitted, fetches from all enabled connectors.
        max_results: Maximum number of records to return (default 200).

    Returns JSON array of records. Equivalent to: python scripts/pipeline.py
    """
    params: dict[str, Any] = {"since": since, "source": source, "max_results": max_results}
    rl = _check_rate_limit("artha_fetch_data")
    if rl:
        return rl
    try:
        source_filter = [source] if source else None
        records = _fetch_in_memory(since, source_filter, max_results)
        _audit("artha_fetch_data", params, f"ok:{len(records)}_records")
        return json.dumps(records, ensure_ascii=False, default=str)
    except Exception as exc:
        _audit("artha_fetch_data", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_health_check(source: str | None = None) -> str:
    """Run connector health checks.

    Args:
        source: Optional connector name. If omitted, checks all enabled connectors.

    Returns JSON object mapping connector name → {healthy: bool, error?: str}.
    """
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
            except Exception as exc:
                out[name] = {"healthy": False, "error": str(exc)}

        _audit("artha_health_check", params, "ok")
        return json.dumps(out, ensure_ascii=False)
    except Exception as exc:
        _audit("artha_health_check", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_read_state(domain: str, pii_redact: bool = True) -> str:
    """Read a domain state file. Auto-decrypts encrypted .age files.
    PII is redacted before returning (configurable).

    Args:
        domain:     Domain name (e.g. "finance", "health", "goals").
                    The .md extension is optional.
        pii_redact: Redact PII patterns before returning (default True).

    Returns the state file text, or JSON error object on failure.
    """
    params: dict[str, Any] = {"domain": domain, "pii_redact": pii_redact}
    rl = _check_rate_limit("artha_read_state")
    if rl:
        return rl
    try:
        content = _read_state_file(domain)
        if pii_redact:
            from pii_guard import filter_text  # type: ignore[import]
            content, _ = filter_text(content)
        _audit("artha_read_state", params, "ok")
        return content
    except FileNotFoundError as exc:
        _audit("artha_read_state", params, f"not_found:{domain}")
        return json.dumps({"error": str(exc)})
    except Exception as exc:
        _audit("artha_read_state", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_pii_scan(text: str) -> str:
    """Scan text for PII patterns without modifying it.

    Args:
        text: Text to scan.

    Returns JSON: {"found": bool, "types": {type_name: count}}.
    """
    params: dict[str, Any] = {"text_length": len(text)}
    try:
        from pii_guard import scan  # type: ignore[import]
        found, type_counts = scan(text)
        _audit("artha_pii_scan", params, f"ok:found={found}")
        return json.dumps({"found": found, "types": type_counts})
    except Exception as exc:
        _audit("artha_pii_scan", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_preflight(quiet: bool = True) -> str:
    """Run pre-catch-up health gate checks.

    Args:
        quiet: Skip live API connectivity checks for faster results (default True).

    Returns JSON array of check results:
        [{name, severity, passed, message, fix_hint?}]
    """
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
    except Exception as exc:
        _audit("artha_preflight", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_list_connectors() -> str:
    """List all configured connectors with name, type, enabled status, and handler.

    Returns JSON array of connector descriptors.
    """
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
    except Exception as exc:
        _audit("artha_list_connectors", {}, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_run_skills(skill_name: str | None = None) -> str:
    """Run data fidelity skills (USCIS case status, visa bulletin, etc.).

    Args:
        skill_name: Optional specific skill to run. If omitted, runs all skills
                    that are due per their cadence configuration.

    Returns JSON mapping skill name → {status, data?, error?}.
    """
    params: dict[str, Any] = {"skill_name": skill_name}
    try:
        from skill_runner import (  # type: ignore[import]
            load_config,
            load_cache,
            should_run,
            run_skill,
        )

        config = load_config()
        cache = load_cache()

        if skill_name:
            # Run a single named skill regardless of cadence
            result = run_skill(skill_name, _REPO_ROOT)
            _audit("artha_run_skills", params, f"ok:{skill_name}={result.get('status')}")
            return json.dumps({skill_name: result}, ensure_ascii=False, default=str)

        # Run all skills that are due
        due = [
            name for name in config.get("skills", {})
            if should_run(name, config, cache)
        ]
        results: dict[str, Any] = {}
        for name in due:
            results[name] = run_skill(name, _REPO_ROOT)

        _audit("artha_run_skills", params, f"ok:{len(results)}_skills")
        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as exc:
        _audit("artha_run_skills", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


# ===========================================================================
# Phase F2 — Write tools (all require approved=True, §3.9.3)
# ===========================================================================

@mcp.tool()
def artha_write_state(
    domain: str,
    content: str,
    approved: bool = False,
) -> str:
    """Write content to a domain state file. Sensitive domains are encrypted automatically.

    ⚠️  WRITE OPERATION — set approved=True to confirm.

    Args:
        domain:   Domain name (e.g. "goals", "home", "learning").
        content:  Full markdown content to write to the state file.
        approved: Must be True to proceed.

    Returns JSON: {status, domain, encrypted}.
    """
    params: dict[str, Any] = {"domain": domain, "content_length": len(content)}
    rejection = _require_approval(approved, "artha_write_state", params)
    if rejection:
        return rejection

    try:
        valid = _known_domains()
        clean = domain.removesuffix(".md")
        if clean not in valid:
            _audit("artha_write_state", params, f"error:unknown_domain={domain}")
            return json.dumps({
                "error": f"Unknown domain '{domain}'.",
                "valid_domains": sorted(valid),
            })

        # Empty-content guard
        if not content.strip():
            _audit("artha_write_state", params, "rejected:empty_content")
            return json.dumps({"error": "Write rejected: content is empty."})

        # Net-negative guard — block writes that would discard >50% of existing content
        try:
            existing = _read_state_file(clean)
            existing_len = len(existing.strip())
            new_len = len(content.strip())
            if existing_len > 100 and new_len < existing_len * 0.5:
                _audit("artha_write_state", params,
                       f"rejected:net_negative existing={existing_len} new={new_len}")
                return json.dumps({
                    "error": "Net-negative write guard: new content is less than 50% "
                             "of existing size. Pass the full updated content.",
                    "existing_bytes": existing_len,
                    "new_bytes": new_len,
                })
        except FileNotFoundError:
            pass  # new domain file — no guard needed

        encrypted = _write_state_file(clean, content)
        _audit("artha_write_state", params, f"ok:encrypted={encrypted}")
        return json.dumps({"status": "ok", "domain": clean, "encrypted": encrypted})
    except Exception as exc:
        _audit("artha_write_state", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_send_email(
    to: str,
    subject: str,
    body: str,
    approved: bool = False,
    archive: bool = False,
) -> str:
    """Send an email via Gmail API.

    ⚠️  WRITE OPERATION — set approved=True to confirm.

    Args:
        to:       Recipient email address(es), comma-separated.
        subject:  Subject line.
        body:     Plain-text body.
        approved: Must be True to proceed.
        archive:  Save body to briefings/YYYY-MM-DD.md (default False).

    Returns JSON with status and message_id.
    """
    params: dict[str, Any] = {
        "to": to, "subject": subject,
        "body_length": len(body), "archive": archive,
    }
    rejection = _require_approval(approved, "artha_send_email", params)
    if rejection:
        return rejection

    try:
        from gmail_send import send_email  # type: ignore[import]
        result = send_email(to=to, subject=subject, body_text=body, archive=archive)
        _audit("artha_send_email", params,
               f"ok:message_id={result.get('message_id', '?')}")
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        _audit("artha_send_email", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


@mcp.tool()
def artha_todo_sync(
    approved: bool = False,
    dry_run: bool = False,
) -> str:
    """Sync open_items.md with Microsoft To Do (push new items + pull completions).

    ⚠️  WRITE OPERATION — set approved=True to confirm.

    Args:
        approved: Must be True to proceed.
        dry_run:  Validate auth without performing actual sync (default False).

    Returns JSON with push/pull result counts.
    """
    params: dict[str, Any] = {"dry_run": dry_run}
    rejection = _require_approval(approved, "artha_todo_sync", params)
    if rejection:
        return rejection

    try:
        from todo_sync import push_items, pull_completions  # type: ignore[import]

        access_token = ""
        if not dry_run:
            from setup_msgraph_oauth import ensure_valid_token  # type: ignore[import]
            token_data = ensure_valid_token()
            access_token = token_data["access_token"]

        push_result = push_items(access_token=access_token, dry_run=dry_run)
        pull_result = pull_completions(access_token=access_token, dry_run=dry_run)
        result = {"pushed": push_result, "pulled": pull_result, "dry_run": dry_run}
        _audit("artha_todo_sync", params, "ok")
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:
        _audit("artha_todo_sync", params, f"error:{exc}")
        return json.dumps({"error": str(exc)})


# ===========================================================================
# Entry point
# ===========================================================================
if __name__ == "__main__":
    mcp.run(transport="stdio")
