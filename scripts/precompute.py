#!/usr/bin/env python3
# pii-guard: ignore-file — dispatcher only; no personal data processed here
"""
scripts/precompute.py — Domain pre-compute dispatcher (Initiative 4, Phase 3).

Replaces agent_scheduler.py + 4 domain agent entry points with a single
command-line interface. Common boilerplate (arg parsing, trace ID, exit code)
runs once; domain-specific logic delegates to the agents/ package.

Usage:
    python scripts/precompute.py --domain capital
    python scripts/precompute.py --domain logistics
    python scripts/precompute.py --domain readiness
    python scripts/precompute.py --domain tribe
    python scripts/precompute.py --all          # run all 4 domains sequentially

Crontab (replaces agent_scheduler.py --tick):
    0 6 * * * /path/to/.venv/bin/python /path/to/scripts/precompute.py --domain capital
    5 6 * * * /path/to/.venv/bin/python /path/to/scripts/precompute.py --domain logistics
    10 6 * * * /path/to/.venv/bin/python /path/to/scripts/precompute.py --domain readiness
    30 6 * * * /path/to/.venv/bin/python /path/to/scripts/precompute.py --domain tribe

Exit codes (compatible with individual agent exit codes):
    0 — success
    1 — agent error
    2 — vault-locked (CapitalAgent only — cron can distinguish)

Ref: specs/simplify.md Initiative 4, Phase 3
"""
from __future__ import annotations

import argparse
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Domain handler registry
# ---------------------------------------------------------------------------
# Maps --domain value → (module path relative to scripts/, function name)
_DOMAIN_HANDLERS: dict[str, tuple[str, str]] = {
    "capital":   ("agents.capital_agent",   "main"),
    "logistics": ("agents.logistics_agent", "main"),
    "readiness": ("agents.readiness_agent", "main"),
    "tribe":     ("agents.tribe_agent",     "main"),
    "career":    ("agents.career_search_agent", "main"),
}


def _run_domain(domain: str) -> int:
    """Import and invoke the domain handler. Returns exit code."""
    module_path, func_name = _DOMAIN_HANDLERS[domain]
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        print(f"⛔ precompute: cannot import {module_path}: {exc}", file=sys.stderr)
        return 1
    handler: Callable[[], int] = getattr(mod, func_name)
    return handler()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Domain pre-compute dispatcher — replaces agent_scheduler.py"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--domain",
        choices=list(_DOMAIN_HANDLERS.keys()),
        help="Run a single domain pre-compute agent",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run all domain agents sequentially",
    )
    args = parser.parse_args()

    if args.all:
        results: dict[str, int] = {}
        for domain in _DOMAIN_HANDLERS:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts}] precompute: starting {domain}...")
            results[domain] = _run_domain(domain)
        failed = [d for d, rc in results.items() if rc != 0]
        if failed:
            print(f"⛔ precompute --all: {len(failed)} domain(s) failed: {failed}", file=sys.stderr)
            return 1
        print(f"✓ precompute --all: all {len(results)} domains completed")
        return 0

    return _run_domain(args.domain)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.exit(main())
