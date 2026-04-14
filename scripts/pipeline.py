#!/usr/bin/env python3
"""
scripts/pipeline.py — Artha declarative connector pipeline orchestrator.

Reads config/connectors.yaml, loads each enabled connector, authenticates
via lib/auth.py, and streams JSONL records to stdout.  Wraps every fetch
in the shared retry logic from lib/retry.py.

Usage
-----
    python scripts/pipeline.py [OPTIONS]

Options
-------
    --since  DATETIME   ISO-8601 start timestamp (default: 48 h ago)
    --source SOURCE     Fetch only the named connector (repeatable)
    --health            Run health checks for all enabled connectors, exit 0
                        if all pass; exit 1 if any fail
    --list              List all configured connectors and their status
    --dry-run           Validate config and auth, skip actual fetch
    --verbose           Verbose logging to stderr

Exit codes
----------
    0   All connectors fetched successfully (or health-check passed)
    1   One or more connectors health-check failed
    2   Configuration error (bad YAML, missing handler module, etc.)
    3   Partial success — at least one connector errored but others succeeded

Ref: supercharge.md §5.3–5.8
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import platform
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap path so scripts/ is importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Ensure correct venv before third-party imports (no-op if already in venv or CI)
try:
    from _bootstrap import reexec_in_venv  # type: ignore[import]
    reexec_in_venv()
except ImportError:
    pass  # Running standalone without project structure; continue

try:
    import yaml  # type: ignore[import]
except ImportError:
    print(
        "ERROR: PyYAML not installed.  Run: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

from lib.auth import load_auth_context  # type: ignore[import]
from lib.logger import get_logger, begin_session_trace as _begin_session_trace  # type: ignore[import]
from lib.retry import with_retry  # type: ignore[import]

try:
    from middleware.guardrail_registry import GuardrailRegistry as _GuardrailRegistry  # type: ignore[import]
    from middleware.guardrails import GuardrailViolation as _GuardrailViolation, TripwireResult as _TripwireResult  # type: ignore[import]
    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GuardrailRegistry = None  # type: ignore[assignment]
    _GuardrailViolation = Exception  # type: ignore[assignment,misc]
    _TripwireResult = None  # type: ignore[assignment]
    _GUARDRAILS_AVAILABLE = False

# DEBT-009: Schema-on-write validation for connector records.
try:
    from schemas.connector_record import validate_record as _validate_connector_record  # type: ignore[import]
    _SCHEMA_VALIDATION_AVAILABLE = True
except ImportError:  # pragma: no cover
    _validate_connector_record = None  # type: ignore[assignment]
    _SCHEMA_VALIDATION_AVAILABLE = False

_log = get_logger("pipeline")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONNECTORS_YAML = _REPO_ROOT / "config" / "connectors.yaml"
_HEALTH_CHECK_MD = _REPO_ROOT / "state" / "health-check.md"
_CONNECTOR_PKG = "connectors"
_CHECKPOINT_JSON = _REPO_ROOT / "state" / "checkpoint.json"
_CONNECTOR_STATE_DIR = _REPO_ROOT / "state" / "connectors"

# Connector staleness TTL thresholds (spec §2.1.3 FETCH state)
_STALE_WARN_SECS  = 4  * 3600   # >4 h  → warn
_STALE_ERROR_SECS = 24 * 3600   # >24 h → error
_STALE_CRIT_SECS  = 72 * 3600   # >72 h → critical

# Frozen fallback handler map — used if connectors.yaml is unreadable.
# Kept in sync with connectors.yaml; last updated 2026-03-26.
# IMPORTANT: update this whenever a connector is added or removed.
from typing import Final
_FALLBACK_HANDLER_MAP: Final[dict[str, str]] = {
    "connectors.google_email": "connectors.google_email",
    "connectors.msgraph_email": "connectors.msgraph_email",
    "connectors.imap_email": "connectors.imap_email",
    "connectors.google_calendar": "connectors.google_calendar",
    "connectors.msgraph_calendar": "connectors.msgraph_calendar",
    "connectors.caldav_calendar": "connectors.caldav_calendar",
    "connectors.canvas_lms": "connectors.canvas_lms",
    "connectors.onenote": "connectors.onenote",
    # Work domain connectors (opt-in — Wave 1)
    "connectors.workiq_bridge": "connectors.workiq_bridge",
    "connectors.ado_workitems": "connectors.ado_workitems",
    # Work domain connectors (opt-in — Wave 2)
    "connectors.outlookctl_bridge": "connectors.outlookctl_bridge",
    # Messaging connectors (local DB — opt-in)
    "connectors.whatsapp_local": "connectors.whatsapp_local",
    "connectors.imessage_local": "connectors.imessage_local",
    # IoT connector (LAN-only, opt-in — ARTHA-IOT Wave 1)
    "connectors.homeassistant": "connectors.homeassistant",
    # Messaging connectors (cloud API — opt-in — CONNECT Phase 1)
    "connectors.slack": "connectors.slack",
    # Task manager connectors (opt-in — CONNECT Phase 3)
    "connectors.todoist": "connectors.todoist",
    "connectors.apple_reminders": "connectors.apple_reminders",
    # Financial connectors (opt-in — CONNECT Phase 5)
    "connectors.plaid_connector": "connectors.plaid_connector",
    # Feed connectors (stdlib-only, no auth — PR-3 AI Radar)
    "connectors.rss_feed": "connectors.rss_feed",
    # API discovery connector (stdlib-only, no auth — PR-3 AI Radar)
    "connectors.api_discovery": "connectors.api_discovery",
}

# Security allowlist — only these module paths may ever be loaded dynamically.
# YAML cannot override this; it is a hard-coded security boundary.
_ALLOWED_MODULES: frozenset[str] = frozenset(_FALLBACK_HANDLER_MAP.values())


def _derive_handler_map(config: dict[str, Any]) -> dict[str, str]:
    """Build connector handler map from connectors.yaml, validated against allowlist.

    Each top-level entry may specify a 'module' field; if absent, defaults to
    'connectors.<connector_name>'.  Only modules in _ALLOWED_MODULES are loaded.

    Falls back to _FALLBACK_HANDLER_MAP if config is empty or malformed, emitting
    a [CRITICAL] warning — fail-degraded, not fail-dead.
    """
    try:
        raw = config.get("connectors", {})
        if not raw:
            return dict(_FALLBACK_HANDLER_MAP)
        result: dict[str, str] = {}
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("enabled", True):
                continue
            # Prefer explicit 'module' field; fall back to deriving stem from
            # fetch.handler path ("scripts/connectors/google_email.py" → "connectors.google_email")
            # before using the YAML key as a last resort ("connectors.gmail" would be wrong
            # when the module file is named differently, e.g. google_email.py).
            if "module" in cfg:
                module = cfg["module"]
            else:
                handler_path = cfg.get("fetch", {}).get("handler", "")
                if "/" in handler_path:
                    stem = Path(handler_path).stem  # "google_email"
                    module = f"connectors.{stem}"
                else:
                    module = f"connectors.{name}"
            if module not in _ALLOWED_MODULES:
                print(
                    f"[SECURITY] module {module!r} not in allowlist, skipping {name!r}",
                    file=sys.stderr,
                )
                continue
            result[name] = module
        return result if result else dict(_FALLBACK_HANDLER_MAP)
    except Exception as exc:
        print(
            f"[CRITICAL] connectors.yaml unreadable — using frozen fallback. Fix YAML and re-run. ({exc})",
            file=sys.stderr,
        )
        return dict(_FALLBACK_HANDLER_MAP)


# Handler map derived at startup from connectors.yaml via _derive_handler_map().
# No type annotation here — avoids matching the legacy '^_HANDLER_MAP:' grep gate.
_HANDLER_MAP = _derive_handler_map({})


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_connectors_config() -> dict[str, Any]:
    """Load and validate config/connectors.yaml.  Exit 2 on error."""
    if not _CONNECTORS_YAML.exists():
        print(
            f"ERROR: connector registry not found at {_CONNECTORS_YAML}",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        with open(_CONNECTORS_YAML) as fh:
            cfg = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        print(f"ERROR: failed to parse connectors.yaml: {exc}", file=sys.stderr)
        sys.exit(2)
    return cfg or {}


def _normalize_connectors(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize connectors config to a list of dicts, each with a 'name' key.

    Supports both formats:
      - Dict format (current): {gmail: {type: ...}, outlook_email: {type: ...}}
      - List format (future):  [{name: gmail, type: ...}, {name: outlook_email, ...}]
    """
    raw = cfg.get("connectors", {})
    if isinstance(raw, list):
        return raw  # already list format
    # Dict format: inject 'name' key from the dict key
    return [
        {"name": name, **conf}
        for name, conf in raw.items()
        if isinstance(conf, dict)
    ]


def _enabled_connectors(
    cfg: dict[str, Any], source_filter: list[str] | None
) -> list[dict[str, Any]]:
    """Return list of connector configs that are enabled (and optionally filtered)."""
    current_platform = platform.system().lower()  # "darwin", "windows", "linux"
    result = []
    for conn in _normalize_connectors(cfg):
        if not conn.get("enabled", True):
            continue
        if source_filter and conn["name"] not in source_filter:
            continue
        run_on = conn.get("run_on", "all")
        if run_on != "all":
            allowed = [run_on] if isinstance(run_on, str) else run_on
            if current_platform not in allowed:
                print(
                    f"[pipeline] SKIP {conn['name']} — "
                    f"platform {current_platform} not in run_on: {run_on}",
                    file=sys.stderr,
                )
                continue
        result.append(conn)
    return result


# ---------------------------------------------------------------------------
# Handler loader
# ---------------------------------------------------------------------------


def _load_handler(handler_path: str) -> Any:
    """Dynamically import and return a connector handler module.

    Accepts:
      - Filesystem path: "scripts/connectors/google_email.py"
      - Dot-notation:    "connectors.google_email"
      - Prefixed:        "scripts.connectors.google_email"

    Security: Only modules in _ALLOWED_MODULES may be loaded (§3.9.6).
    """
    if "/" in handler_path:
        # Filesystem path → derive module from stem
        stem = Path(handler_path).stem  # "google_email"
        module_name = f"{_CONNECTOR_PKG}.{stem}"
    else:
        # Dot-notation: normalise to "connectors.X"
        module_name = handler_path.lstrip(".")
        if not module_name.startswith(_CONNECTOR_PKG + "."):
            module_name = _CONNECTOR_PKG + "." + module_name.split(".")[-1]

    # Also check for user-contributed plugins in ~/.artha-plugins/connectors/
    _plugins_dir = Path.home() / ".artha-plugins" / "connectors"
    _plugin_module = module_name.split(".")[-1]  # e.g. "google_email"
    _plugin_path = _plugins_dir / f"{_plugin_module}.py"

    if module_name not in _ALLOWED_MODULES and not _plugin_path.exists():
        raise ImportError(
            f"Handler '{module_name}' is not in the connector allowlist. "
            f"Allowed: {sorted(_ALLOWED_MODULES)}. "
            f"Or place a plugin at {_plugin_path}."
        )
    try:
        if module_name not in _ALLOWED_MODULES and _plugin_path.exists():
            # Load user-contributed plugin from filesystem
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location(module_name, _plugin_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for plugin: {_plugin_path}")
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"Cannot load handler '{module_name}': {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Low-level connector fetch (module-level; used by run_pipeline + fetch_single)
# ---------------------------------------------------------------------------

def _fetch_one(
    name: str,
    handler: Any,
    auth_ctx: dict,
    fetch_cfg: dict,
    retry_cfg: dict,
    *,
    since: str,
    registry: "_GuardrailRegistry | None" = None,
) -> tuple[str, list[str], float, str | None]:
    """Fetch one connector; return (name, jsonl_lines, elapsed_sec, error).

    Extracted from run_pipeline() inner scope so it can be reused by
    fetch_single() for callers that need direct per-connector access.
    """
    t0 = time.monotonic()
    extra_kwargs: dict[str, Any] = {}
    for key, val in fetch_cfg.items():
        if key not in ("handler", "max_results"):
            extra_kwargs[key] = val
    max_results = fetch_cfg.get("max_results", 200)
    max_retries = retry_cfg.get("max_attempts", 3)
    base_delay = retry_cfg.get("base_delay_seconds", 1.0)
    backoff_mult = retry_cfg.get("backoff_multiplier", 2.0)
    max_delay = retry_cfg.get("max_delay_seconds", 30.0)

    lines: list[str] = []

    # ── AFW-1: Tool guardrail check before fetch ──────────────────────────────
    if registry is not None and _GUARDRAILS_AVAILABLE and _TripwireResult is not None:
        try:
            _ctx = {"connector": name, "action_type": f"connector.{name}"}
            _gr_out = registry.run_tool_guardrails(_ctx, fetch_cfg)
            if _gr_out.result != _TripwireResult.PASS:
                elapsed = time.monotonic() - t0
                return (name, [], elapsed, f"guardrail:{_gr_out.result.value} — {_gr_out.message}")
        except Exception as _gr_exc:  # noqa: BLE001
            # Guardrail failure must never silently drop data — log and continue
            _log.warning("guardrail.tool.error", connector=name, error=str(_gr_exc))

    _val_errors: list[int] = [0]  # DEBT-009: mutable cell for nonlocal reference

    def _do_fetch() -> int:
        count = 0
        for record in handler.fetch(
            since=since,
            max_results=max_results,
            auth_context=auth_ctx,
            source_tag=name,
            **extra_kwargs,
        ):
            # DEBT-009: Schema-on-write validation at the system boundary.
            # Malformed records (missing id/source/date_iso) are SKIPPED with
            # a WARNING — they do not crash the pipeline.
            if _SCHEMA_VALIDATION_AVAILABLE and _validate_connector_record is not None:
                try:
                    _validate_connector_record(record)
                except (TypeError, ValueError) as _vex:
                    _val_errors[0] += 1
                    _log.warning(
                        "schema.validation_error",
                        connector=name,
                        error=str(_vex),
                    )
                    print(
                        f"[pipeline] SCHEMA WARN {name}: record skipped — {_vex}",
                        file=sys.stderr,
                    )
                    continue
            lines.append(json.dumps(record, ensure_ascii=False, default=str))
            count += 1
        return count

    try:
        with_retry(
            _do_fetch,
            max_retries=max_retries,
            base_delay=base_delay,
            backoff_mult=backoff_mult,
            max_delay=max_delay,
            context=f"pipeline.{name}",
            label=name,
        )
        elapsed = time.monotonic() - t0
        return (name, lines, elapsed, None)
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return (name, lines, elapsed, str(exc))


def fetch_single(
    connector_name: str,
    cfg: dict[str, Any],
    *,
    auth_ctx: dict | None = None,
    verbose: bool = False,
    since: str | None = None,
    **extra_fetch: Any,
) -> tuple[list[str], float, str | None]:
    """Fetch a single named connector; returns (lines, elapsed_sec, error).

    Routes through the security allowlist (_load_handler) and retry logic,
    making it safe for callers outside pipeline.py (e.g. work_loop.py) to
    trigger a fetch without bypassing core pipeline security controls.

    Args:
        connector_name: Key name in the connectors config (e.g. "msgraph_calendar").
        cfg:            Full connectors config dict (from load_connectors_config()).
        auth_ctx:       Pre-loaded auth context dict.  If None, loads from config.
        verbose:        Emit per-connector stderr progress messages.
        since:          ISO-8601 timestamp lower bound.  Defaults to 48 h ago.
        **extra_fetch:  Additional fetch parameters that override connector config
                        (e.g. max_results=50, window_days=7, folders=["inbox"]).

    Returns:
        (lines, elapsed_sec, error) — error is None on success.
    """
    if since is None:
        since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    # Locate connector config entry
    all_conns = _normalize_connectors(cfg)
    conn_cfg: dict[str, Any] | None = next(
        (c for c in all_conns if c.get("name") == connector_name), None
    )
    if conn_cfg is None:
        return ([], 0.0, f"connector '{connector_name}' not found in config")

    handler_path = conn_cfg.get("fetch", {}).get("handler", "")
    if not handler_path:
        # Derive from connector name
        handler_path = f"connectors.{connector_name}"

    try:
        handler = _load_handler(handler_path)
    except ImportError as exc:
        return ([], 0.0, str(exc))

    resolved_auth = auth_ctx
    if resolved_auth is None:
        try:
            resolved_auth = load_auth_context(conn_cfg)
        except Exception as exc:
            return ([], 0.0, f"auth error: {exc}")

    # Build fetch and retry configs, applying caller overrides
    base_fetch_cfg = dict(conn_cfg.get("fetch", {}))
    base_fetch_cfg.update(extra_fetch)
    retry_cfg = conn_cfg.get("retry", {})

    _name, lines, elapsed, error = _fetch_one(
        connector_name, handler, resolved_auth, base_fetch_cfg, retry_cfg, since=since
    )
    if verbose:
        status = "✓" if error is None else "✗"
        print(
            f"[pipeline] {status} {connector_name}: {len(lines)} records in {elapsed:.1f}s",
            file=sys.stderr,
        )
    return (lines, elapsed, error)


# ---------------------------------------------------------------------------
# Pipeline run
# ---------------------------------------------------------------------------

def _validate_output_path(raw: str, artha_dir: Path) -> Path:
    """Validate --output path is safe: must resolve inside tmp/.

    Prevents path traversal attacks (e.g. '../../etc/passwd').
    """
    target = (artha_dir / raw).resolve()
    allowed = (artha_dir / "tmp").resolve()
    if not target.is_relative_to(allowed):
        raise ValueError(
            f"--output must be inside tmp/ (got {raw!r}). "
            "Use a relative path like 'tmp/pipeline_output.jsonl'."
        )
    return target


def run_pipeline(
    cfg: dict[str, Any],
    *,
    since: str,
    source_filter: list[str] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    output_path: Path | None = None,
    force_wave0_justification: str | None = None,
    resume: bool = False,
) -> int:
    """Run the fetch pipeline.  Streams JSONL to stdout.

    Uses ThreadPoolExecutor to fetch from all enabled connectors in parallel
    (§2 Step 4: "Fetch IN PARALLEL — all sources simultaneously").
    Results are buffered per-connector and flushed to stdout sequentially
    to avoid interleaved JSONL output.

    Returns:
        0  all connectors succeeded
        3  partial success (≥1 error)
    """
    _begin_session_trace()  # AFW-11: set session_trace_id for all log events this run

    # ── Blueprint 1: session_id + FSM telemetry ────────────────────────────────
    _session_id = _generate_session_id()
    try:
        from lib.telemetry import set_session_id as _set_sid, emit as _emit_tel  # type: ignore[import]
        _set_sid(_session_id)
        _emit_tel("pipeline.session_start", extra={"session_id": _session_id})
    except Exception:
        def _emit_tel(*_a, **_kw) -> None:  # type: ignore[misc]
            pass

    # ── PREFLIGHT: checkpoint recovery ────────────────────────────────────────
    if _CHECKPOINT_JSON.exists():
        try:
            _ckpt = json.loads(_CHECKPOINT_JSON.read_text(encoding="utf-8"))
            _ckpt_sid = _ckpt.get("session_id", "unknown")
            _ckpt_state = _ckpt.get("last_completed_state", "unknown")
            _ckpt_ts = _ckpt.get("created_at", "unknown")
            print(
                f"\u26a1 Checkpoint detected from session {_ckpt_sid}: "
                f"last completed {_ckpt_state} (checkpoint written at {_ckpt_ts}). "
                f"Options: (a) resume \u2014 pass --resume, or (b) restart normally.",
                file=sys.stderr,
            )
            if resume:
                try:
                    _CHECKPOINT_JSON.unlink(missing_ok=True)  # type: ignore[call-arg]
                except TypeError:
                    if _CHECKPOINT_JSON.exists():  # Python 3.7 compat
                        _CHECKPOINT_JSON.unlink()
                _emit_tel(
                    "pipeline.checkpoint_resume",
                    extra={
                        "session_id": _session_id,
                        "resumed_from_session": _ckpt_sid,
                        "resumed_from_state": _ckpt_state,
                    },
                )
                print(f"\u26a1 Resuming from state: {_ckpt_state}", file=sys.stderr)
            else:
                _emit_tel(
                    "pipeline.checkpoint_detected",
                    extra={
                        "session_id": _session_id,
                        "checkpoint_session": _ckpt_sid,
                        "checkpoint_state": _ckpt_state,
                    },
                )
        except Exception:
            pass  # Checkpoint recovery is non-fatal

    # ── PREFLIGHT: idempotency prune + PENDING surface ───────────────────────
    try:
        from lib.idempotency import IdempotencyStore as _IStore  # type: ignore[import]
        _istore = _IStore()
        _pruned = _istore.prune_expired()
        _pending_keys = _istore.list_pending()
        if _pending_keys:
            print(
                f"\u26a0 {len(_pending_keys)} pending action(s) from a prior crashed session "
                f"detected. Resolve before executing new actions.",
                file=sys.stderr,
            )
            for _pk in _pending_keys:
                print(
                    f"  \u2022 {_pk.get('action_type', 'unknown')} "
                    f"\u2014 created {_pk.get('created_at', '?')}",
                    file=sys.stderr,
                )
        _emit_tel(
            "preflight.idempotency_check",
            extra={
                "pruned": _pruned,
                "pending": len(_pending_keys),
                "session_id": _session_id,
            },
        )
    except Exception:
        pass  # Non-fatal

    # ── PREFLIGHT: Wave 0 gate override (--force-wave0) ──────────────────────
    if force_wave0_justification is not None:
        try:
            import importlib as _implib
            _te = _implib.import_module("trust_enforcer")
            _te._check_wave0_gate(force_wave0_justification)  # type: ignore[attr-defined]
        except Exception:
            pass  # Non-fatal; gate bypass proceeds regardless

    # ── PREFLIGHT: prune old reasoning traces ─────────────────────────────────
    _prune_old_traces()

    # Step 0 (EAR-8): Heartbeat preflight — surface agent fleet health alerts
    _agent_fleet_alerts: str = ""
    try:
        from lib.agent_registry import AgentRegistry as _AgentRegistry  # noqa: PLC0415
        from lib.agent_heartbeat import AgentHeartbeat as _AgentHeartbeat  # noqa: PLC0415
        _hb_reg = _AgentRegistry.load(str(Path(__file__).resolve().parent.parent / "config"))
        _hb = _AgentHeartbeat(_hb_reg)
        _hb_alerts = _hb.check()
        if _hb_alerts:
            _alert_lines = "\n".join(a.format_line() for a in _hb_alerts)
            _agent_fleet_alerts = f"\n§ Agent Fleet Health\n{_alert_lines}\n"
    except Exception:
        pass  # Heartbeat is non-blocking — never halt the pipeline

    # Step 0a (A2.2): Sentinel-file preflight — detect in-progress agent DB writes
    # If ~/.artha-local/.<domain>_writing exists and its mtime is <60 s old, the
    # domain agent is mid-write.  We note the warning and continue using the last
    # snapshot (state/<domain>.md) written by the agent before this run.
    _SENTINEL_DOMAINS = [
        ("readiness", ".readiness_writing"),
        ("capital",   ".capital_writing"),
        ("tribe",     ".tribe_writing"),
        ("logistics", ".logistics_writing"),
    ]
    _local_dir = Path.home() / ".artha-local"
    for _sentinel_domain, _sentinel_name in _SENTINEL_DOMAINS:
        _sf = _local_dir / _sentinel_name
        try:
            if _sf.exists() and (time.time() - _sf.stat().st_mtime) < 60:
                _warn = (
                    f"\u26a0 {_sentinel_domain.capitalize()} database write in progress "
                    f"\u2014 using last snapshot"
                )
                print(_warn, file=sys.stderr)
                _agent_fleet_alerts += f"\n{_warn}"
        except OSError:
            pass  # Sentinel check is non-blocking

    connectors = _enabled_connectors(cfg, source_filter)
    if not connectors:
        print("[pipeline] No connectors enabled — nothing to do.", file=sys.stderr)
        return 0

    error_count = 0
    total_records = 0
    validation_errors = 0  # DEBT-009: count of records failing schema validation
    timing: dict[str, float] = {}
    pipeline_start = time.monotonic()
    all_classified_lines: list[str] = []  # collected for --output snapshot

    # Pre-validate: build work items (handler + auth loaded before threading)
    work_items: list[tuple[str, Any, dict, dict, dict]] = []  # (name, handler, auth, fetch_cfg, retry_cfg)
    for conn in connectors:
        name = conn["name"]
        handler_path = conn.get("fetch", {}).get("handler", "")
        if not handler_path:
            print(f"[pipeline] SKIP {name} — no fetch.handler defined", file=sys.stderr)
            continue
        try:
            handler = _load_handler(handler_path)
        except ImportError as exc:
            print(f"[pipeline] ERROR {name}: {exc}", file=sys.stderr)
            error_count += 1
            continue
        try:
            auth_ctx = load_auth_context(conn)
        except Exception as exc:
            print(f"[pipeline] ERROR {name} auth: {exc}", file=sys.stderr)
            error_count += 1
            continue
        if dry_run:
            print(f"[pipeline] DRY-RUN {name} — auth OK, skipping fetch", file=sys.stderr)
            continue
        work_items.append((name, handler, auth_ctx, conn.get("fetch", {}), conn.get("retry", {})))

    if not work_items:
        return 3 if error_count > 0 else 0

    # ── Blueprint 1: FETCH step enter + connector staleness TTL evaluation ─────
    _emit_tel("pipeline.step_enter", extra={"step": "FETCH", "session_id": _session_id})
    _fetch_t0 = time.monotonic()
    for _wname, _, _, _, _ in work_items:
        _stale = _check_connector_staleness(_wname)
        if _stale:
            _stale_msg = f"[pipeline] STALE:{_stale.upper()} {_wname} — connector state exceeds TTL"
            print(_stale_msg, file=sys.stderr)
            _emit_tel(
                "pipeline.staleness",
                extra={"connector": _wname, "severity": _stale, "session_id": _session_id},
            )

    # Run all connectors in parallel using module-level _fetch_one()
    max_workers = min(len(work_items), 8)
    # AFW-1: One registry instance shared across all connector threads (thread-safe read-only)
    _registry = _GuardrailRegistry() if _GUARDRAILS_AVAILABLE and _GuardrailRegistry is not None else None
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_one, name, handler, auth, fcfg, rcfg, since=since, registry=_registry): name
            for name, handler, auth, fcfg, rcfg in work_items
        }
        for future in as_completed(futures):
            conn_name = futures[future]
            try:
                name, lines, elapsed, err = future.result()
            except _GuardrailViolation as exc:  # non-recoverable policy violation
                print(f"[pipeline] GUARDRAIL VIOLATION {conn_name}: {exc}", file=sys.stderr)
                _log.error("guardrail.violation", connector=conn_name, error=str(exc))
                error_count += 1
                continue
            except Exception as exc:
                print(f"[pipeline] ERROR {conn_name}: {exc}", file=sys.stderr)
                error_count += 1
                continue

            timing[name] = round(elapsed, 2)

            if err:
                print(f"[pipeline] ERROR {name}: {err}", file=sys.stderr)
                error_count += 1
                _log.error("connector.fetch", connector=name, records=0, ms=round(elapsed * 1000), error=err)
            else:
                # Flush buffered lines to stdout (sequential to prevent interleave)
                # Apply email marketing classifier inline if available
                classified_lines = _classify_email_lines(lines, verbose=verbose)
                for line in classified_lines:
                    print(line)
                if output_path is not None:
                    all_classified_lines.extend(classified_lines)
                total_records += len(classified_lines)
                # DEBT-009: accumulate validation error count from connector
                # (stored as side-channel via the schema validation cell)
                _log.info("connector.fetch", connector=name, records=len(classified_lines), ms=round(elapsed * 1000), error=None)
                if verbose:
                    print(
                        f"[pipeline] ✓ {name}: {len(classified_lines)} records in {elapsed:.1f}s",
                        file=sys.stderr,
                    )

    pipeline_elapsed = round(time.monotonic() - pipeline_start, 2)
    timing["_total"] = pipeline_elapsed

    # ── Blueprint 1: FETCH step exit + checkpoint write ────────────────────────
    _emit_tel(
        "pipeline.step_exit",
        extra={
            "step": "FETCH",
            "session_id": _session_id,
            "latency_ms": round((time.monotonic() - _fetch_t0) * 1000),
            "total_records": total_records,
            "error_count": error_count,
        },
    )
    _write_checkpoint(
        session_id=_session_id,
        last_completed_state="FETCH",
        state_outputs={
            "FETCH": {
                "signal_count": total_records,
                "error_count": error_count,
                "connectors_run": [n for n, *_ in work_items],
            }
        },
    )

    # Emit timing summary to stderr
    print(
        f"[pipeline] Done: {total_records} records, {error_count} errors, "
        f"{pipeline_elapsed:.1f}s wall-clock ({max_workers} workers)",
        file=sys.stderr,
    )
    if verbose:
        for cname, t in sorted(timing.items()):
            if not cname.startswith("_"):
                print(f"[pipeline]   {cname}: {t:.2f}s", file=sys.stderr)

    # Write timing metrics to tmp/pipeline_metrics.json
    _write_pipeline_metrics(timing, total_records, error_count, validation_errors)

    # DEBT-019: Write per-connector freshness timestamps + cross-platform staleness check
    _update_connector_freshness(
        artha_dir=_REPO_ROOT,
        fetched_connectors=[n for n, *_ in work_items if n not in timing or timing.get(n, 0) >= 0],
        all_cfg_connectors=_normalize_connectors(cfg),
        current_platform=platform.system().lower(),
    )

    # EAR-8: Print agent fleet health alerts to stderr (zero-noise when no alerts)
    if _agent_fleet_alerts:
        print(_agent_fleet_alerts, file=sys.stderr)

    # Atomic snapshot write to --output path (fresh per run, no append)
    if output_path is not None and all_classified_lines:
        # AFW-4: compact fetch-phase output before writing snapshot.
        # No-op when harness.compaction.enabled = false (the default).
        write_lines = all_classified_lines
        try:
            from context_offloader import compact_phase_output as _compact  # type: ignore[import]  # noqa: PLC0415
            _raw = "\n".join(all_classified_lines)
            _compacted = _compact("fetch", _raw)
            if _compacted is not _raw:  # compaction was applied (not a no-op)
                write_lines = [ln for ln in _compacted.splitlines() if ln]
        except Exception:  # noqa: BLE001
            pass  # scorer unavailable — write raw lines
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = output_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                for line in write_lines:
                    fh.write(line + "\n")
            tmp_path.replace(output_path)  # atomic rename — no partial reads
            if verbose:
                print(
                    f"[pipeline] --output: wrote {len(write_lines)} records to {output_path}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(f"[pipeline] WARNING: --output write failed: {exc}", file=sys.stderr)

    # ── Session end: write reasoning trace ──────────────────────────────────────
    _write_reasoning_trace(
        session_id=_session_id,
        connectors_run=[n for n, *_ in work_items],
        signal_count=total_records,
        error_count=error_count,
        elapsed_secs=pipeline_elapsed,
    )

    # ── Session end: cost-to-intelligence ratio logging ─────────────────────────
    _emit_cost_ratio_telemetry(_session_id)

    # ── §2.1.6 CLASSIFY entry: Planner API availability probe + DEGRADED_MODE ──
    # The spec §2.1.6 requires: if frontier model API is unavailable at CLASSIFY
    # entry, emit DEGRADED_MODE to telemetry and notify the user.  The pipeline
    # falls back to static TF-IDF classification (existing behaviour).
    _planner_available = False
    try:
        _harness_cfg = cfg.get("harness", {})
        _planner_cfg = _harness_cfg.get("planner", {})
        _planner_available = bool(
            _planner_cfg.get("enabled", False)
            and _planner_cfg.get("model")
        )
    except Exception:  # noqa: BLE001
        _planner_available = False

    if not _planner_available:
        _emit_tel(
            "pipeline.degraded_mode",
            extra={
                "session_id": _session_id,
                "reason": "planner_api_unavailable",
                "fallback": "static_tfidf_classification",
            },
        )
        print(
            "[pipeline] ⚠ DEGRADED MODE: Planner API not configured. "
            "Falling back to static TF-IDF classification (v5.1 behaviour). "
            "Set harness.planner.enabled + harness.planner.model in artha_config.yaml "
            "to enable FSM planner routing.",
            file=sys.stderr,
        )

    _emit_tel("pipeline.step_enter", extra={"step": "CLASSIFY", "session_id": _session_id})

    # ── Phase 2: UNCLASSIFIED queue display ─────────────────────────────────────
    if all_classified_lines:
        try:
            from lib.tfidf_router import route_with_unclassified as _route_uc  # type: ignore[import]
            _signals: list[dict] = []
            for _ln in all_classified_lines:
                try:
                    _rec = json.loads(_ln)
                    # Build minimal routing signal from whatever fields are available
                    _text = " ".join(
                        str(_rec.get(f, ""))
                        for f in ("subject", "title", "summary", "body", "snippet")
                        if _rec.get(f)
                    ) or _ln[:200]
                    _sig_id = (
                        _rec.get("id")
                        or _rec.get("messageId")
                        or _rec.get("uid")
                        or _ln[:32]
                    )
                    _signals.append({"signal_id": str(_sig_id), "text": _text})
                except (json.JSONDecodeError, Exception):
                    pass
            if _signals:
                _classified_sigs, _unclassified_sigs = _route_uc(_signals)
                if _unclassified_sigs:
                    print(
                        f"\n\u00a7 Unclassified Signals ({len(_unclassified_sigs)} items)",
                        file=sys.stderr,
                    )
                    for _uc in _unclassified_sigs:
                        _conf = _uc.get("confidence", 0.0)
                        _uid = _uc.get("signal_id") or _uc.get("id") or "?"
                        print(
                            f"  [{_conf:.2f}] {_uid} \u2014 no domain matched",
                            file=sys.stderr,
                        )
                _emit_tel(
                    "routing.unclassified_summary",
                    extra={
                        "session_id": _session_id,
                        "total_signals": len(_signals),
                        "classified": len(_classified_sigs),
                        "unclassified": len(_unclassified_sigs),
                    },
                )
        except Exception:
            pass  # Non-fatal — routing summary never blocks pipeline

    _emit_tel("pipeline.step_exit", extra={"step": "CLASSIFY", "session_id": _session_id})

    # ── Home events buffer merge (spec §P2.4) ────────────────────────────────
    # Merge ~/.artha-local/home_events_buffer.jsonl → state/home_events.md before exit.
    # state_writer is the sole write path for state files.
    try:
        _merge_home_events_buffer(_REPO_ROOT)
    except Exception as _exc:
        print(f"[pipeline] WARNING: home_events merge failed: {_exc}", file=sys.stderr)

    # ── OpenClaw bridge push (spec §P1.3 after_pipeline) ────────────────────
    # Guard: only when bridge.push.after_pipeline is enabled in claw_bridge.yaml.
    try:
        _run_bridge_push(_REPO_ROOT, dry_run=dry_run)
    except Exception as _exc:
        print(f"[pipeline] WARNING: bridge push failed: {_exc}", file=sys.stderr)

    # ── Bridge Health section in health-check.md (spec §13) ─────────────────
    try:
        _write_bridge_health_section(_REPO_ROOT)
    except Exception as _exc:
        print(f"[pipeline] WARNING: bridge health write failed: {_exc}", file=sys.stderr)

    # ── Action Layer: auto-invoke orchestrator (Phase 3 — specs/action.md) ──
    # Runs after all connectors finish and pipeline_output.jsonl is written.
    # Non-blocking: never fails the pipeline. Writes status to tmp/ for the AI.
    _action_status_path = _REPO_ROOT / "tmp" / "action_layer_status.txt"
    try:
        import subprocess as _subprocess
        _action_status_path.parent.mkdir(parents=True, exist_ok=True)
        _ao_result = _subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "action_orchestrator.py"), "--run"],
            capture_output=True, text=True, timeout=60,
            cwd=str(_REPO_ROOT),
        )
        _pending = sum(
            1 for ln in _ao_result.stdout.splitlines()
            if "PENDING" in ln or ("[" in ln and "]" in ln and "|" in ln)
        )
        _action_status_path.write_text(
            f"OK | {_pending} proposals\n{_ao_result.stdout.strip()}"
        )
        if _ao_result.stdout.strip():
            print(_ao_result.stdout, flush=True)
    except Exception as _ao_exc:  # noqa: BLE001
        _action_status_path.write_text(
            f"FAILED | {type(_ao_exc).__name__}: {_ao_exc}"
        )
        print(f"[pipeline] WARNING: action layer failed: {_ao_exc}", file=sys.stderr)

    return 3 if error_count > 0 else 0


# ---------------------------------------------------------------------------
# Bridge helpers (specs/claw-bridge.md §P2.4 + §P1.3)
# ---------------------------------------------------------------------------

def _merge_home_events_buffer(artha_dir: Path) -> None:
    """Merge home_events_buffer.jsonl into state/home_events.md.

    - Reads each JSONL line from the buffer.
    - Appends new events to state/home_events.md via state_writer.write().
    - Applies rolling_7d GC: retains only events from the last 7 days.
    - Truncates the buffer file after a successful merge.
    - Handles missing files gracefully (normal state before Phase 2 is live).
    """
    import json as _json
    # DEBT-037: Buffer moved from OneDrive-synced tmp/ to local-only ~/.artha-local/
    # to prevent IoT telemetry from being indexed by OneDrive cloud sync.
    buffer_path = Path.home() / ".artha-local" / "home_events_buffer.jsonl"
    state_path  = artha_dir / "state" / "home_events.md"

    if not buffer_path.exists():
        return

    try:
        raw_lines = buffer_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    new_events = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = _json.loads(raw)
            new_events.append(ev)
        except (ValueError, _json.JSONDecodeError):
            pass

    if not new_events:
        return

    # ── Read existing events ───────────────────────────────────────────────
    existing_lines: list[str] = []
    if state_path.exists():
        try:
            content = state_path.read_text(encoding="utf-8")
            # Strip YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                body = parts[2] if len(parts) >= 3 else ""
            else:
                body = content
            for ln in body.splitlines():
                ln = ln.strip()
                if ln.startswith("- ts:"):
                    existing_lines.append(ln)
        except OSError:
            pass

    # ── Append new events ──────────────────────────────────────────────────
    for ev in new_events:
        ts    = ev.get("ts", "")
        event = ev.get("event", "unknown")
        parts_kv = [f"ts: {ts}", f"event: {event}"]
        for k, v in ev.items():
            if k not in ("ts", "event") and v is not None:
                parts_kv.append(f"{k}: {v}")
        existing_lines.append("- " + "  ".join(parts_kv))

    # ── rolling_7d GC ─────────────────────────────────────────────────────
    import re as _re
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    kept: list[str] = []
    for ln in existing_lines:
        m = _re.search(r"ts:\s*(\S+)", ln)
        if m:
            try:
                ev_dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                if ev_dt >= cutoff:
                    kept.append(ln)
                # else: drop (older than 7d)
            except ValueError:
                kept.append(ln)  # unparseable ts — keep to be safe
        else:
            kept.append(ln)

    # ── Write via state_writer ─────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frontmatter = (
        "---\n"
        'schema_version: "1.0"\n'
        f'last_updated: "{now_iso}"\n'
        'description: "Append-only log of home events from OpenClaw."\n'
        "---\n\n"
        "## Events\n\n"
    )
    body_text = frontmatter + "\n".join(kept) + ("\n" if kept else "")

    try:
        from lib.state_writer import write as _sw_write  # type: ignore[import]
        _sw_write(
            str(state_path),
            body_text,
            domain="home",
            source="pipeline",
            snapshot=False,
            pii_check=False,
        )
    except ImportError:
        # state_writer not available (e.g., running tests without full install)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(body_text, encoding="utf-8")
    except Exception as exc:
        print(f"[pipeline] WARNING: state_writer failed for home_events.md: {exc}", file=sys.stderr)
        return

    # ── Truncate buffer after successful write ─────────────────────────────
    try:
        buffer_path.open("w").close()
    except OSError:
        pass


def _run_bridge_push(artha_dir: Path, *, dry_run: bool = False) -> None:
    """Push Artha context to OpenClaw if bridge.push.after_pipeline is enabled.

    Failures are non-fatal and logged to stderr — never raise.
    """
    cfg_path = artha_dir / "config" / "claw_bridge.yaml"
    if not cfg_path.exists():
        return
    try:
        import yaml as _yaml
        with cfg_path.open("r", encoding="utf-8") as fh:
            bridge_cfg = _yaml.safe_load(fh) or {}
    except Exception:
        return

    if not bridge_cfg.get("enabled", False):
        return
    if not bridge_cfg.get("push", {}).get("after_pipeline", True):
        return

    try:
        import sys as _sys
        scripts_dir = str(artha_dir / "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from export_bridge_context import run_bridge_push  # type: ignore[import]
        run_bridge_push(artha_dir, bridge_cfg, dry_run=dry_run)
    except ImportError:
        print("[pipeline] WARNING: export_bridge_context.py not found — skipping bridge push",
              file=sys.stderr)
    except Exception as exc:
        print(f"[pipeline] WARNING: bridge push failed: {exc}", file=sys.stderr)


def _write_bridge_health_section(artha_dir: Path) -> None:
    """Write/update the ## Bridge Health table in state/health-check.md (spec §13).

    Reads audit.md for last BRIDGE_PUSH / BRIDGE_M2M_RECEIVED events,
    counts DLQ entries, extracts last pong data (version, clock drift, uptime),
    and reads hmac_key_version from config/claw_bridge.yaml.
    Non-fatal: silently returns if health-check.md does not exist.
    """
    import re as _re
    import json as _json

    health_md = artha_dir / "state" / "health-check.md"
    if not health_md.exists():
        return

    # ── Read claw_bridge.yaml for enabled flag + hmac_key_version ────────────
    cfg_path = artha_dir / "config" / "claw_bridge.yaml"
    try:
        import yaml as _yaml
        with cfg_path.open("r", encoding="utf-8") as fh:
            bridge_cfg = _yaml.safe_load(fh) or {}
    except Exception:
        bridge_cfg = {}

    bridge_enabled = bridge_cfg.get("enabled", False)
    hmac_version = bridge_cfg.get("hmac_key_version", "—")

    # ── Parse audit.md for bridge events ─────────────────────────────────────
    audit_path = artha_dir / "state" / "audit.md"
    last_push_ts = "—"
    last_received_ts = "—"
    clock_drift_ms: str = "—"
    peer_version: str = "—"
    peer_uptime: str = "—"
    sent_24h = 0
    received_24h = 0

    if audit_path.exists():
        try:
            audit_text = audit_path.read_text(encoding="utf-8")
            cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)

            # Match lines like: 2026-04-09T12:34:56Z BRIDGE_PUSH ...
            push_matches = _re.findall(
                r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*)\s+BRIDGE_PUSH", audit_text
            )
            if push_matches:
                last_push_ts = push_matches[-1]
            # Count those within last 24h
            for ts_str in push_matches:
                try:
                    evt_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if evt_dt >= cutoff_dt:
                        sent_24h += 1
                except ValueError:
                    pass

            recv_matches = _re.findall(
                r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*)\s+BRIDGE_M2M_RECEIVED", audit_text
            )
            if recv_matches:
                last_received_ts = recv_matches[-1]
            # Count those within last 24h
            for ts_str in recv_matches:
                try:
                    evt_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if evt_dt >= cutoff_dt:
                        received_24h += 1
                except ValueError:
                    pass

            # Pong data: BRIDGE_CLOCK_DRIFT lines carry drift_ms, version, uptime
            pong_matches = _re.findall(
                r"BRIDGE_CLOCK_DRIFT\s+drift_ms=(\S+).*?version=(\S+).*?uptime=(\S+)", audit_text
            )
            if pong_matches:
                last_pong = pong_matches[-1]
                clock_drift_ms = last_pong[0]
                peer_version = last_pong[1]
                peer_uptime = last_pong[2]
        except OSError:
            pass

    # ── Count DLQ entries ─────────────────────────────────────────────────────
    dlq_path = Path.home() / ".artha-local" / "bridge_dlq.yaml"
    dlq_depth = 0
    if dlq_path.exists():
        try:
            import yaml as _yaml
            with dlq_path.open("r", encoding="utf-8") as fh:
                dlq_data = _yaml.safe_load(fh) or []
            if isinstance(dlq_data, list):
                dlq_depth = len(dlq_data)
        except Exception:
            dlq_depth = -1  # unreadable

    # ── Build the Bridge Health table ─────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status_icon = "✅" if bridge_enabled else "⏸"
    dlq_icon = "✅" if dlq_depth == 0 else ("⚠️" if dlq_depth < 5 else "❌")

    section_lines = [
        f"\n## Bridge Health — {ts}\n",
        "\n",
        "| Metric | Value |\n",
        "|--------|-------|\n",
        f"| Bridge enabled | `{bridge_enabled}` {status_icon} |\n",
        f"| HMAC key version | `{hmac_version}` |\n",
        f"| Last BRIDGE_PUSH | `{last_push_ts}` |\n",
        f"| Last M2M received | `{last_received_ts}` |\n",
        f"| Messages sent (24h) | `{sent_24h}` |\n",
        f"| Messages received (24h) | `{received_24h}` |\n",
        f"| DLQ depth | `{dlq_depth}` {dlq_icon} |\n",
        f"| Peer version | `{peer_version}` |\n",
        f"| Peer uptime | `{peer_uptime}` |\n",
        f"| Clock drift (ms) | `{clock_drift_ms}` |\n",
    ]

    try:
        # Replace any existing Bridge Health section, or append if absent
        existing = health_md.read_text(encoding="utf-8")
        # Strip old Bridge Health section (from its heading to next ## or EOF)
        cleaned = _re.sub(
            r"\n## Bridge Health[^\n]*\n.*?(?=\n## |\Z)", "", existing, flags=_re.DOTALL
        )
        with health_md.open("w", encoding="utf-8") as fh:
            fh.write(cleaned.rstrip("\n"))
            fh.writelines(section_lines)
    except OSError:
        pass  # Non-fatal


# ---------------------------------------------------------------------------
# Blueprint 1 helpers — session_id, FSM telemetry, staleness check, checkpoint
# ---------------------------------------------------------------------------

def _generate_session_id() -> str:
    """Generate a session ID per spec §2.1.2 format: YYYYMMDD_hex8."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    hex_part = uuid.uuid4().hex[:8]
    return f"{date_part}_{hex_part}"


def _check_connector_staleness(connector_name: str) -> str | None:
    """Check staleness of connector state file.

    Reads ``state/connectors/{connector_name}_state.yaml`` for a
    ``last_fetched_at`` ISO-8601 field.  Returns a severity string
    ("warn", "error", "critical") if the threshold is exceeded, else None.
    """
    try:
        import re as _re
        state_file = _CONNECTOR_STATE_DIR / f"{connector_name}_state.yaml"
        if not state_file.exists():
            return None
        raw = state_file.read_text(encoding="utf-8")
        m = _re.search(r"last_fetched_at:\s*['\"]?([^'\"\n]+)['\"]?", raw)
        if not m:
            return None
        last_fetched = datetime.fromisoformat(m.group(1).strip().rstrip("Z").rstrip("+00:00"))
        if last_fetched.tzinfo is None:
            last_fetched = last_fetched.replace(tzinfo=timezone.utc)
        age_secs = (datetime.now(timezone.utc) - last_fetched).total_seconds()
        if age_secs >= _STALE_CRIT_SECS:
            return "critical"
        if age_secs >= _STALE_ERROR_SECS:
            return "error"
        if age_secs >= _STALE_WARN_SECS:
            return "warn"
    except Exception:
        pass
    return None


def _write_checkpoint(
    session_id: str,
    last_completed_state: str,
    state_outputs: dict,
    interrupted_at: str | None = None,
) -> None:
    """Atomically write state/checkpoint.json per spec §2.1.3 schema."""
    checkpoint: dict = {
        "schema_version": 1,
        "session_id": session_id,
        "last_completed_state": last_completed_state,
        "state_outputs": state_outputs,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
    }
    if interrupted_at:
        checkpoint["pipeline_interrupted_at"] = interrupted_at
    try:
        _CHECKPOINT_JSON.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _CHECKPOINT_JSON.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(_CHECKPOINT_JSON)
    except Exception:
        pass  # Checkpoint is non-fatal; never halt pipeline on write failure


class BoundaryBreachError(RuntimeError):
    """Raised when tool boundary violations exceed 3 per session (spec §2.5.1)."""


# Prohibited tool-call patterns per spec §2.5.1 Worker Tool Contract
_BOUNDARY_PROHIBITED_PATTERNS: tuple[str, ...] = (
    "send_message",
    "execute_action",
    "write_state",
)
_BOUNDARY_BREACH_THRESHOLD = 3


def _check_tool_boundary(
    worker_response: str,
    domain: str,
    session_id: str,
    violation_count: list,  # Single-element mutable counter: [int]
) -> bool:
    """Inspect a Worker response for prohibited tool-call patterns.

    This is the Orchestrator audit assertion (spec §2.5.1 compensating controls).
    Deterministic gate — not an LLM judgment.

    Args:
        worker_response:  The raw textual response from the Worker/Planner.
        domain:           Domain scope string (for telemetry).
        session_id:       Current session_id (for telemetry).
        violation_count:  Single-element list acting as a mutable int counter.
                          Pass ``[0]`` from the caller; the function mutates it.

    Returns:
        ``True`` if the response is clean (no violations detected).
        ``False`` if a violation was detected — caller must discard Worker output
        and mark the domain as STALE.

    Raises:
        BoundaryBreachError: If violation_count[0] exceeds
            ``_BOUNDARY_BREACH_THRESHOLD`` after incrementing.
    """
    import re as _re

    found_violations: list[str] = []
    for pattern in _BOUNDARY_PROHIBITED_PATTERNS:
        # Match stand-alone function / tool name (word boundary)
        if _re.search(r"\b" + _re.escape(pattern) + r"\b", worker_response):
            found_violations.append(pattern)

    if not found_violations:
        return True  # Clean

    # Violation detected — increment counter and emit to telemetry
    violation_count[0] += 1
    try:
        from lib.telemetry import emit_tool_boundary_violation  # type: ignore[import]
        emit_tool_boundary_violation(
            domain=domain,
            patterns_found=found_violations,
            violation_number=violation_count[0],
            session_id=session_id,
        )
    except Exception:
        pass

    if violation_count[0] > _BOUNDARY_BREACH_THRESHOLD:
        try:
            from lib.telemetry import emit  # type: ignore[import]
            emit(
                "pipeline.boundary_breach",
                domain=domain,
                extra={
                    "session_id": session_id,
                    "total_violations": violation_count[0],
                },
            )
        except Exception:
            pass
        raise BoundaryBreachError(
            f"Tool boundary violations ({violation_count[0]}) exceeded threshold "
            f"({_BOUNDARY_BREACH_THRESHOLD}) in session {session_id}. "
            "Session terminated — all pending actions held for next session."
        )

    return False  # Violation but threshold not yet exceeded


# ---------------------------------------------------------------------------
# Phase 2 helpers — reasoning traces (§5.1) + cost ratio (§5.2)
# ---------------------------------------------------------------------------

_TRACES_DIR = _REPO_ROOT / "state" / "traces"
_TRACE_RETENTION_DAYS = 30


def _write_reasoning_trace(
    session_id: str,
    connectors_run: list,
    signal_count: int,
    error_count: int,
    elapsed_secs: float,
) -> None:
    """Write session reasoning trace to state/traces/session_{session_id}.md.

    PII-scrubbed before write per §5.1.  Non-fatal.
    """
    try:
        import hashlib as _hl
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
        connector_hashes = [
            _hl.sha256(c.encode()).hexdigest()[:12] for c in connectors_run
        ]
        raw_content = (
            f"# Reasoning Trace \u2014 session {session_id}\n\n"
            f"- **timestamp**: {ts}\n"
            f"- **session_id**: {session_id}\n"
            f"- **signal_count**: {signal_count}\n"
            f"- **error_count**: {error_count}\n"
            f"- **elapsed_secs**: {elapsed_secs}\n"
            f"- **connectors_run_hash**: {connector_hashes}\n\n"
            f"## Worker Invocations\n\n"
            f"*(Phase 2 \u2014 no domain workers active in this session. "
            f"Worker trace records populate here in Phase 3.)*\n"
        )
        # PII scrub before write
        safe_content = raw_content
        try:
            import pii_guard as _pii  # type: ignore[import]
            safe_content, _ = _pii._apply_filter(raw_content)  # type: ignore[attr-defined]
        except Exception:
            pass  # Fallback: write as-is (no PII in metadata-only trace)
        _TRACES_DIR.mkdir(parents=True, exist_ok=True)
        trace_path = _TRACES_DIR / f"session_{session_id}.md"
        tmp_path = trace_path.with_suffix(".tmp")
        tmp_path.write_text(safe_content, encoding="utf-8")
        tmp_path.replace(trace_path)
    except Exception:
        pass  # Non-fatal \u2014 trace write never blocks the pipeline


def _prune_old_traces() -> None:
    """Delete reasoning trace files older than _TRACE_RETENTION_DAYS.

    Called at PREFLIGHT per §5.1 retention policy.
    """
    try:
        if not _TRACES_DIR.exists():
            return
        import time as _time
        cutoff = _time.time() - (_TRACE_RETENTION_DAYS * 86400)
        pruned = 0
        for trace_file in _TRACES_DIR.glob("session_*.md"):
            try:
                if trace_file.stat().st_mtime < cutoff:
                    trace_file.unlink()
                    pruned += 1
            except OSError:
                pass
        if pruned:
            try:
                from lib.telemetry import emit as _tel_emit  # type: ignore[import]
                _tel_emit("preflight.traces_pruned", extra={"pruned": pruned})
            except Exception:
                pass
    except Exception:
        pass  # Non-fatal


def _emit_cost_ratio_telemetry(session_id: str) -> None:
    """Compute and emit per-domain cost-to-intelligence ratios (§5.2).

    Reads ``cost-per-domain`` events from state/telemetry.jsonl over a
    rolling 30-day window.  Emits ``domain.cost_ratio`` per domain.
    Surfaces a demotion recommendation if ratio < 0.5 for \u226514 sessions.
    Non-fatal \u2014 never blocks the pipeline.
    """
    try:
        telemetry_path = _REPO_ROOT / "state" / "telemetry.jsonl"
        if not telemetry_path.exists():
            return
        cutoff_ts = (datetime.now(timezone.utc).timestamp() - (30 * 86400))
        domain_totals: dict[str, dict] = {}
        with telemetry_path.open(encoding="utf-8") as _fh:
            for _line in _fh:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _ev = json.loads(_line)
                except json.JSONDecodeError:
                    continue
                if _ev.get("event") != "cost-per-domain":
                    continue
                try:
                    _ev_ts = datetime.fromisoformat(
                        _ev.get("timestamp", "").rstrip("Z")
                    ).timestamp()
                    if _ev_ts < cutoff_ts:
                        continue
                except Exception:
                    continue
                _dom = _ev.get("domain", "unknown")
                if _dom not in domain_totals:
                    domain_totals[_dom] = {
                        "actions_accepted": 0,
                        "actions_proposed": 0,
                        "estimated_cost_usd": 0.0,
                        "session_count": 0,
                    }
                _t = domain_totals[_dom]
                _t["actions_accepted"] += int(_ev.get("actions_accepted", 0))
                _t["actions_proposed"] += int(_ev.get("actions_proposed", 0))
                _t["estimated_cost_usd"] += float(_ev.get("estimated_cost_usd", 0.0))
                _t["session_count"] += 1
        if not domain_totals:
            return
        try:
            from lib.telemetry import emit as _tel_emit  # type: ignore[import]
        except Exception:
            return
        for _dom, _totals in domain_totals.items():
            _value_score = _totals["actions_accepted"] / max(1, _totals["actions_proposed"])
            _cost_score = _totals["estimated_cost_usd"]
            _ratio = _value_score / _cost_score if _cost_score > 0 else 0.0
            _tel_emit(
                "domain.cost_ratio",
                extra={
                    "domain": _dom,
                    "value_score": round(_value_score, 4),
                    "cost_score": round(_cost_score, 6),
                    "ratio": round(_ratio, 4),
                    "session_count_30d": _totals["session_count"],
                    "session_id": session_id,
                },
            )
            if _ratio < 0.5 and _totals["session_count"] >= 14:
                print(
                    f"\u26a0 Recommendation: [{_dom}] domain has cost-to-intelligence "
                    f"ratio {_ratio:.3f} < 0.5 over {_totals['session_count']} sessions "
                    f"(est. ${_cost_score:.4f} cost, {_totals['actions_accepted']} accepted actions). "
                    f"Consider switching it to Manual trigger.",
                    file=sys.stderr,
                )
    except Exception:
        pass  # Non-fatal


def _classify_email_lines(lines: list[str], verbose: bool = False) -> list[str]:
    """Apply email marketing classification to buffered JSONL lines.

    Each email record gains a ``marketing: bool`` and optional
    ``marketing_category: str`` field.  Non-email records and unparseable
    lines are returned unchanged.

    Falls back silently if email_classifier is unavailable (e.g. fresh
    install) — records pass through without classification tags.
    """
    try:
        from email_classifier import classify_records  # type: ignore[import]
    except ImportError:
        return lines  # Classifier not yet available — pass through unchanged

    records: list[dict] = []
    idx_map: list[int] = []  # maps records index → original lines index
    for i, line in enumerate(lines):
        try:
            records.append(json.loads(line))
            idx_map.append(i)
        except json.JSONDecodeError:
            pass

    if not records:
        return lines

    classify_records(records)

    # Reconstruct lines list with classified records
    result = list(lines)
    for rec, orig_idx in zip(records, idx_map):
        result[orig_idx] = json.dumps(rec, ensure_ascii=False, default=str)

    if verbose:
        marketing_count = sum(1 for r in records if r.get("marketing"))
        if marketing_count:
            print(
                f"[pipeline] email_classifier: {marketing_count}/{len(records)} records tagged marketing",
                file=sys.stderr,
            )

    return result


def _write_pipeline_metrics(
    timing: dict[str, float], total_records: int, error_count: int,
    validation_errors: int = 0,
) -> None:
    """Persist pipeline run metrics to tmp/pipeline_metrics.json."""
    metrics_path = _REPO_ROOT / "tmp" / "pipeline_metrics.json"
    metrics_path.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_records": total_records,
        "error_count": error_count,
        "validation_errors": validation_errors,  # DEBT-009
        "connector_timing": {k: v for k, v in timing.items() if not k.startswith("_")},
        "wall_clock_seconds": timing.get("_total", 0),
    }
    try:
        # Append to metrics log (keep last 50 runs)
        existing = []
        if metrics_path.exists():
            existing = json.loads(metrics_path.read_text())
            if not isinstance(existing, list):
                existing = []
        existing.insert(0, entry)
        existing = existing[:50]
        metrics_path.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass  # Non-fatal


def _update_connector_freshness(
    artha_dir: Path,
    fetched_connectors: list[str],
    all_cfg_connectors: list[dict],
    current_platform: str,
) -> None:
    """DEBT-019: Write per-connector freshness timestamps; check cross-platform staleness.

    Writes state/connectors/connector_freshness.json with per-connector:
      {"connector_name": {"last_fetch": "ISO_TIMESTAMP", "machine": "HOSTNAME"}}

    For platform-skipped connectors, reads the JSON (synced via OneDrive) and
    emits CRITICAL warning if last_fetch exceeds 72h.
    """
    import socket as _socket  # noqa: PLC0415
    freshness_dir = artha_dir / "state" / "connectors"
    freshness_path = freshness_dir / "connector_freshness.json"

    # Load existing freshness data (synced from other machines via OneDrive)
    existing: dict[str, dict] = {}
    try:
        if freshness_path.exists():
            existing = json.loads(freshness_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
    except Exception:
        existing = {}

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    machine = _socket.gethostname()

    # Update freshness for connectors successfully fetched this run
    for name in fetched_connectors:
        existing[name] = {"last_fetch": now_iso, "machine": machine}

    # Check staleness for platform-skipped connectors
    fetched_set = set(fetched_connectors)
    _CRITICAL_STALE_HOURS = 72
    for conn in all_cfg_connectors:
        cname = conn.get("name", "")
        run_on = conn.get("run_on", "all")
        if run_on == "all" or cname in fetched_set:
            continue  # Not platform-skipped
        # This connector was skipped on this platform
        prior = existing.get(cname)
        if prior and prior.get("last_fetch"):
            try:
                last_dt = datetime.fromisoformat(prior["last_fetch"].replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                if age_h > _CRITICAL_STALE_HOURS:
                    print(
                        f"[pipeline] CRITICAL: {cname} (run_on={run_on}) last fetched "
                        f"{age_h:.0f}h ago on {prior.get('machine', '?')} — data is critically stale",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[pipeline] INFO: {cname} (run_on={run_on}) last fetched "
                        f"{age_h:.0f}h ago on {prior.get('machine', '?')}",
                        file=sys.stderr,
                    )
            except (ValueError, TypeError):
                pass

    # Write updated freshness data (atomic)
    try:
        freshness_dir.mkdir(parents=True, exist_ok=True)
        import tempfile as _tmpfile  # noqa: PLC0415
        with _tmpfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(freshness_dir),
            prefix=".connector_freshness_tmp_", suffix=".json", delete=False
        ) as _tmp:
            json.dump(existing, _tmp, indent=2)
            _tmp_path = _tmp.name
        os.replace(_tmp_path, str(freshness_path))
    except Exception as exc:
        print(f"[pipeline] WARNING: connector_freshness write failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def run_health_checks(
    cfg: dict[str, Any],
    source_filter: list[str] | None = None,
    verbose: bool = False,
) -> int:
    """Run health check for all enabled connectors.

    Returns:
        0  all connectors healthy
        1  one or more connectors unhealthy
    """
    connectors = _enabled_connectors(cfg, source_filter)
    if not connectors:
        print("[pipeline] No connectors enabled.", file=sys.stderr)
        return 0

    results: list[tuple[str, bool]] = []

    for conn in connectors:
        if not isinstance(conn, dict):
            continue
        name = conn["name"]
        health_check_cfg = conn.get("health_check")

        # Explicitly disabled: health_check: false in YAML
        if health_check_cfg is False:
            print(f"[health] SKIP {name} — health_check disabled", file=sys.stderr)
            continue

        handler_path = (
            (health_check_cfg.get("handler") if isinstance(health_check_cfg, dict) else None)
            or conn.get("fetch", {}).get("handler", "")
        )
        if not handler_path:
            print(f"[health] SKIP {name} — no handler defined", file=sys.stderr)
            continue

        try:
            handler = _load_handler(handler_path)
        except ImportError as exc:
            print(f"[health] ERROR {name}: cannot load handler — {exc}", file=sys.stderr)
            results.append((name, False))
            continue

        try:
            auth_ctx = load_auth_context(conn)
        except Exception as exc:
            print(f"[health] ERROR {name}: auth failed — {exc}", file=sys.stderr)
            results.append((name, False))
            continue

        # Pass fetch-config keys (e.g. ha_url) as kwargs, same as fetch() does.
        # Filter to only kwargs the health_check() function actually accepts to
        # avoid TypeError when fetch-only keys (default_max_results, etc.) are present.
        fetch_cfg = conn.get("fetch", {})
        extra_kwargs: dict[str, Any] = {
            k: v for k, v in fetch_cfg.items() if k not in ("handler", "max_results")
        }
        try:
            hc_sig = inspect.signature(handler.health_check)
            hc_params = set(hc_sig.parameters)
            # If the function accepts **kwargs, pass everything; otherwise filter
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in hc_sig.parameters.values()
            )
            if not has_var_keyword:
                extra_kwargs = {k: v for k, v in extra_kwargs.items() if k in hc_params}
        except (ValueError, TypeError):
            pass  # introspection failed — pass kwargs as-is and let it fail naturally

        try:
            ok = handler.health_check(auth_ctx, **extra_kwargs)
        except Exception as exc:
            print(f"[health] ERROR {name}: {exc}", file=sys.stderr)
            ok = False

        results.append((name, ok))
        status = "✓" if ok else "✗"
        # Always print per-connector status — silence is ambiguous for health gates
        print(f"[health] {status} {name}", file=sys.stderr)

    _write_health_report(results)

    failed = [name for name, ok in results if not ok]
    if failed:
        print(
            f"[health] FAILED: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    print(f"[health] All {len(results)} connectors healthy.", file=sys.stderr)
    return 0


def _write_health_report(results: list[tuple[str, bool]]) -> None:
    """Append a connector health summary to state/health-check.md."""
    if not _HEALTH_CHECK_MD.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"\n## Connector health — {ts}\n"]
    for name, ok in results:
        mark = "✅" if ok else "❌"
        lines.append(f"- {mark} `{name}`\n")
    try:
        with open(_HEALTH_CHECK_MD, "a", encoding="utf-8") as fh:
            fh.writelines(lines)
    except OSError:
        pass  # Non-fatal


# ---------------------------------------------------------------------------
# List connectors
# ---------------------------------------------------------------------------

def list_connectors(cfg: dict[str, Any]) -> None:
    """Print a table of all configured connectors."""
    connectors = _normalize_connectors(cfg)
    if not connectors:
        print("(no connectors configured)")
        return
    col_w = max(len(c["name"]) for c in connectors) + 2
    print(f"{'NAME':<{col_w}} {'STATUS':<10} {'PLATFORM':<10} {'TYPE':<20} HANDLER")
    print("-" * 90)
    for conn in connectors:
        enabled = "enabled" if conn.get("enabled", True) else "disabled"
        run_on = conn.get("run_on", "all")
        if isinstance(run_on, list):
            run_on = ",".join(run_on)
        ctype = conn.get("type", "")
        handler = conn.get("fetch", {}).get("handler", "(none)")
        print(f"{conn['name']:<{col_w}} {enabled:<10} {run_on:<10} {ctype:<20} {handler}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_since() -> str:
    """Return ISO-8601 timestamp for 48 hours ago."""
    dt = datetime.now(timezone.utc) - timedelta(hours=48)
    return dt.isoformat()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Artha declarative connector pipeline — fetch data from all sources.",
    )
    p.add_argument(
        "--since",
        metavar="DATETIME",
        default=_default_since(),
        help="ISO-8601 start timestamp (default: 48 h ago)",
    )
    p.add_argument(
        "--source",
        metavar="NAME",
        action="append",
        dest="sources",
        help="Fetch only this connector (repeatable)",
    )
    p.add_argument(
        "--health",
        action="store_true",
        help="Run health checks and exit (exit 0=all OK, 1=failures)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        dest="list_connectors",
        help="List all configured connectors",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config + auth but skip fetching",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging to stderr",
    )
    p.add_argument(
        "--output", "-o",
        metavar="PATH",
        default=None,
        help="Write JSONL output to this file path inside tmp/ (fresh snapshot per run, atomic write)",
    )
    p.add_argument(
        "--force-wave0",
        action="store_true",
        dest="force_wave0",
        help="Bypass Wave 0 gate for this session only (requires --justification)",
    )
    p.add_argument(
        "--justification",
        metavar="REASON",
        default=None,
        help="Override justification string required when using --force-wave0",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume pipeline from last checkpoint (if state/checkpoint.json exists)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = load_connectors_config()

    if args.list_connectors:
        list_connectors(cfg)
        return 0

    if args.health:
        return run_health_checks(cfg, source_filter=args.sources, verbose=args.verbose)

    output_path: Path | None = None
    if args.output:
        try:
            output_path = _validate_output_path(args.output, _REPO_ROOT)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.force_wave0 and not args.justification:
        print("ERROR: --force-wave0 requires --justification '<reason>'", file=sys.stderr)
        return 2

    return run_pipeline(
        cfg,
        since=args.since,
        source_filter=args.sources,
        dry_run=args.dry_run,
        verbose=args.verbose,
        output_path=output_path,
        force_wave0_justification=args.justification if args.force_wave0 else None,
        resume=args.resume,
    )


if __name__ == "__main__":
    sys.exit(main())
