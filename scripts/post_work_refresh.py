#!/usr/bin/env python3
# pii-guard: ignore-file — orchestrator only; no personal data stored here
"""scripts/post_work_refresh.py — Non-blocking work state refresh triggered
after catch-up delivery (finalize.md Step 11d).

Design mirrors post_catchup_memory.py: single-responsibility, non-blocking,
observable, kill-switchable.

Usage:
    python scripts/post_work_refresh.py
    python scripts/post_work_refresh.py --dry-run
    python scripts/post_work_refresh.py --quiet

Config kill-switch (user_profile.yaml):
    work:
      refresh:
        run_on_catchup: false   # default: true

Writes one structured record to state/work/eval/work-refresh-log.jsonl.
Prints one line to stdout:
    [wr-YYYYMMDD-HHMMSS] work refresh ok (READ mode, 0 errors)

Non-blocking — catch-up completes regardless of errors from this script.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_ARTHA_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_id() -> str:
    return datetime.now(tz=timezone.utc).strftime("wr-%Y%m%d-%H%M%S")


def _is_enabled(artha_dir: Path) -> bool:
    """Check work.refresh.run_on_catchup in user_profile.yaml (default: true)."""
    try:
        import yaml  # noqa: PLC0415
        profile = yaml.safe_load(
            (artha_dir / "config" / "user_profile.yaml").read_text(encoding="utf-8")
        ) or {}
        work_section = profile.get("work") or {}
        refresh_section = work_section.get("refresh") or {}
        return bool(refresh_section.get("run_on_catchup", True))
    except Exception:  # noqa: BLE001
        return True  # default on when config is unreadable


def _append_run_log(record: dict, artha_dir: Path) -> None:
    """Append a structured run record to state/work/eval/work-refresh-log.jsonl."""
    log_path = artha_dir / "state" / "work" / "eval" / "work-refresh-log.jsonl"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # Non-fatal


# ---------------------------------------------------------------------------
# Core refresh
# ---------------------------------------------------------------------------

def run(artha_dir: Path, dry_run: bool = False) -> dict:
    """Trigger a work state refresh via WorkLoop.

    Uses READ mode (not REFRESH): avoids live connector I/O during catch-up
    delivery — the loop still re-evaluates learned state and updates frontmatter.

    Returns a dict with run metadata.
    """
    t0 = time.monotonic()
    record: dict = {
        "run_id": _run_id(),
        "mode": "READ",
        "dry_run": dry_run,
        "errors": 0,
        "duration_ms": 0,
        "error": None,
    }

    if dry_run:
        record["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return record

    try:
        from work_loop import WorkLoop, LoopMode  # noqa: PLC0415
        loop = WorkLoop(mode=LoopMode.READ)
        result = loop.run()
        record["errors"] = len(result.errors)
        record["stages"] = result.stages_completed
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)
        record["errors"] = 1

    record["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return record


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-catch-up work state refresh (Step 11d).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would run without executing the work loop.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output (errors still go to stderr).",
    )
    parser.add_argument(
        "--artha-dir",
        metavar="DIR",
        default=str(_ARTHA_DIR),
        help="Artha project root (default: auto-detected from script location).",
    )
    args = parser.parse_args(argv)
    artha_dir = Path(args.artha_dir).resolve()

    if not _is_enabled(artha_dir):
        if not args.quiet:
            print("ℹ️  post_work_refresh disabled via work.refresh.run_on_catchup.")
        return 0

    result = run(artha_dir, dry_run=args.dry_run)

    # Append observability log (skip on dry-run)
    if not args.dry_run:
        _append_run_log(result, artha_dir)

    # One-line status
    if not args.quiet:
        prefix = "🔍 [dry-run]" if args.dry_run else f"[{result['run_id']}]"
        if result.get("error"):
            print(f"{prefix} ⚠️  work refresh error: {result['error']}", file=sys.stderr)
            return 2
        errors_str = f", {result['errors']} error(s)" if result["errors"] else ""
        print(f"{prefix} work refresh ok (READ mode{errors_str})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
