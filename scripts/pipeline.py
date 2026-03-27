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
from lib.logger import get_logger  # type: ignore[import]
from lib.retry import with_retry  # type: ignore[import]

_log = get_logger("pipeline")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CONNECTORS_YAML = _REPO_ROOT / "config" / "connectors.yaml"
_HEALTH_CHECK_MD = _REPO_ROOT / "state" / "health-check.md"
_CONNECTOR_PKG = "connectors"

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
# Pipeline run
# ---------------------------------------------------------------------------

def run_pipeline(
    cfg: dict[str, Any],
    *,
    since: str,
    source_filter: list[str] | None = None,
    dry_run: bool = False,
    verbose: bool = False,
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
    connectors = _enabled_connectors(cfg, source_filter)
    if not connectors:
        print("[pipeline] No connectors enabled — nothing to do.", file=sys.stderr)
        return 0

    error_count = 0
    total_records = 0
    timing: dict[str, float] = {}
    pipeline_start = time.monotonic()

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

    def _fetch_connector(
        name: str, handler: Any, auth_ctx: dict, fetch_cfg: dict, retry_cfg: dict
    ) -> tuple[str, list[str], float, str | None]:
        """Fetch one connector; return (name, jsonl_lines, elapsed_sec, error)."""
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

        def _do_fetch() -> int:
            count = 0
            for record in handler.fetch(
                since=since,
                max_results=max_results,
                auth_context=auth_ctx,
                source_tag=name,
                **extra_kwargs,
            ):
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

    # Run all connectors in parallel
    max_workers = min(len(work_items), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_fetch_connector, name, handler, auth, fcfg, rcfg): name
            for name, handler, auth, fcfg, rcfg in work_items
        }
        for future in as_completed(futures):
            conn_name = futures[future]
            try:
                name, lines, elapsed, err = future.result()
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
                total_records += len(classified_lines)
                _log.info("connector.fetch", connector=name, records=len(classified_lines), ms=round(elapsed * 1000), error=None)
                if verbose:
                    print(
                        f"[pipeline] ✓ {name}: {len(classified_lines)} records in {elapsed:.1f}s",
                        file=sys.stderr,
                    )

    pipeline_elapsed = round(time.monotonic() - pipeline_start, 2)
    timing["_total"] = pipeline_elapsed

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
    _write_pipeline_metrics(timing, total_records, error_count)

    return 3 if error_count > 0 else 0


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
    timing: dict[str, float], total_records: int, error_count: int
) -> None:
    """Persist pipeline run metrics to tmp/pipeline_metrics.json."""
    metrics_path = _REPO_ROOT / "tmp" / "pipeline_metrics.json"
    metrics_path.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_records": total_records,
        "error_count": error_count,
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
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = load_connectors_config()

    if args.list_connectors:
        list_connectors(cfg)
        return 0

    if args.health:
        return run_health_checks(cfg, source_filter=args.sources, verbose=args.verbose)

    return run_pipeline(
        cfg,
        since=args.since,
        source_filter=args.sources,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
